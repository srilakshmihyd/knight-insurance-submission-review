import pytest

from app.merge import (
    Invalid,
    merge_all,
    merge_field,
    merge_loss_runs,
    merge_schedule_count,
    norm_count,
    norm_date,
    norm_radius,
    norm_state,
    norm_yes_no,
)
from app.models import LLM_FIELDS, AttachmentEntry, CsvCount, LossRunsResult, SourceExtraction, SourceField


def _source(name: str, kind: str = "email", **answers: str | None) -> SourceExtraction:
    """A successful extraction. Listed fields are stated with a matching excerpt; others not mentioned."""
    fields = []
    for field in LLM_FIELDS:
        if field in answers:
            value = answers[field]
            excerpt = f"{field}: {value if value is not None else 'Not provided'}"
            locator = "email body" if kind == "email" else "PDF page 1"
            fields.append(SourceField(field=field, source_file=name, value_text=value, excerpt=excerpt,
                                      locator=locator))
        else:
            fields.append(SourceField(field=field, source_file=name, value_text=None, excerpt=None, locator=None))
    return SourceExtraction(source_file=name, source_kind=kind, fields=fields)


EMAIL = "broker_email.txt"
PDF = "application.pdf"


# --- normalizers ---


def test_state_names_and_codes_become_codes():
    assert norm_state("California") == norm_state("ca") == "CA"
    assert norm_state("Texas") == norm_state("TX") == "TX"
    assert norm_state(" new  york. ") == "NY"
    for text in ("Calif", "ZZ", "Ontario"):
        with pytest.raises(Invalid):
            norm_state(text)


def test_radius_150_and_150_point_0_are_the_same():
    assert norm_radius("150") == norm_radius("150.0") == 150
    assert norm_radius("150.1") == 150.1


def test_radius_with_units_and_commas():
    for text in ("150 miles", "150 mi", "150-mile", "150 Miles."):
        assert norm_radius(text) == 150
    assert norm_radius("1,200 miles") == 1200


def test_radius_negative_hedged_or_not_numeric_is_invalid():
    for text in ("-5", "about 100", "under 150 miles", "100-200 miles", "far", "150 km"):
        with pytest.raises(Invalid):
            norm_radius(text)


def test_count_must_be_a_nonnegative_whole_number():
    assert norm_count("0") == 0
    assert norm_count("3.0") == 3
    assert norm_count("4 vehicles") == norm_count("four") == norm_count("Four trucks") == 4
    for text in ("-1", "2.5", "about 4", "several"):
        with pytest.raises(Invalid):
            norm_count(text)


def test_yes_no_become_true_false_and_vague_is_invalid():
    assert norm_yes_no("Yes") is True
    assert norm_yes_no("no") is False
    assert norm_yes_no("N") is False
    for text in ("maybe", "occasionally"):
        with pytest.raises(Invalid):
            norm_yes_no(text)


def test_common_date_formats_become_yyyy_mm_dd():
    for text in ("2026-11-01", "11/01/2026", "11/1/2026", "Nov 1, 2026", "Nov. 1, 2026",
                 "November 1st, 2026", "1 November 2026"):
        assert norm_date(text) == "2026-11-01"


def test_impossible_or_unclear_date_is_invalid():
    for text in ("2026-02-30", "02/30/2026", "next month", "Nov 2026", "ASAP"):
        with pytest.raises(Invalid):
            norm_date(text)


# --- combining sources ---


def test_same_value_in_both_sources_is_known():
    merged = merge_field("garaging_state", [_source(EMAIL, garaging_state="CA"),
                                            _source(PDF, "application", garaging_state="California")])
    assert merged.state == "KNOWN"
    assert merged.value == "CA"
    assert len(merged.evidence) == 2


def test_harmless_differences_are_not_conflicts():
    sources = [_source(EMAIL, business_name="Example LLC", operations_description="Local delivery.",
                       operating_radius_miles="150"),
               _source(PDF, "application", business_name="EXAMPLE llc", operations_description="Local delivery",
                       operating_radius_miles="150.0")]
    for field in ("business_name", "operations_description", "operating_radius_miles"):
        assert merge_field(field, sources).state == "KNOWN"


def test_disagreement_is_conflict_with_no_value_and_both_answers_kept():
    merged = merge_field("operating_radius_miles", [_source(EMAIL, operating_radius_miles="180"),
                                                    _source(PDF, "application", operating_radius_miles="80")])
    assert merged.state == "CONFLICT"
    assert merged.value is None
    assert {(e.source_file, e.value) for e in merged.evidence} == {(EMAIL, 180), (PDF, 80)}
    assert all(e.excerpt and e.locator for e in merged.evidence)


def test_value_in_one_source_only_is_known_not_conflict():
    merged = merge_field("hazardous_materials", [_source(EMAIL, hazardous_materials="No"), _source(PDF, "application")])
    assert merged.state == "KNOWN"
    assert merged.value is False


def test_not_provided_everywhere_is_missing_and_lists_checked_files():
    merged = merge_field("requested_effective_date", [_source(EMAIL, requested_effective_date=None),
                                                      _source(PDF, "application", requested_effective_date=None)])
    assert merged.state == "MISSING"
    assert merged.value is None
    assert merged.checked_sources == [EMAIL, PDF]
    assert len(merged.evidence) == 2  # the "Not provided" lines are kept as proof


def test_answer_not_in_its_own_excerpt_is_invalid():
    invented = SourceField(field="operating_radius_miles", source_file=EMAIL, value_text="100",
                           excerpt="Operating radius miles: 180", locator="email body")
    merged = merge_field("operating_radius_miles",
                         [SourceExtraction(source_file=EMAIL, source_kind="email", fields=[invented])])
    assert (merged.state, merged.value) == ("INVALID", None)
    assert "does not appear" in merged.note


def test_answer_must_match_whole_words_in_excerpt():
    partial = SourceField(field="operating_radius_miles", source_file=EMAIL, value_text="150",
                          excerpt="Operating radius: 1500 miles", locator="email body")
    merged = merge_field("operating_radius_miles",
                         [SourceExtraction(source_file=EMAIL, source_kind="email", fields=[partial])])
    assert merged.state == "INVALID"


def test_excerpt_not_found_in_source_is_invalid():
    unplaced = SourceField(field="business_name", source_file=PDF, value_text="Example LLC",
                           excerpt="Applicant: Example LLC", locator=None)
    merged = merge_field("business_name",
                         [SourceExtraction(source_file=PDF, source_kind="application", fields=[unplaced])])
    assert (merged.state, merged.value) == ("INVALID", None)
    assert "not found in the source" in merged.note


def test_texas_and_tx_are_not_a_conflict():
    merged = merge_field("garaging_state", [_source(EMAIL, garaging_state="TX"),
                                            _source(PDF, "application", garaging_state="Texas")])
    assert (merged.state, merged.value) == ("KNOWN", "TX")


def test_vague_answer_is_invalid():
    merged = merge_field("hazardous_materials", [_source(EMAIL, hazardous_materials="maybe")])
    assert merged.state == "INVALID"
    assert merged.value is None
    assert "maybe" in merged.note


def test_failed_llm_source_is_not_counted_as_checked():
    failed = SourceExtraction(source_file=PDF, source_kind="application", error="LLM unavailable: x")
    merged = merge_field("business_name", [_source(EMAIL, business_name="Example LLC"), failed])
    assert merged.state == "KNOWN"
    assert merged.checked_sources == [EMAIL]
    assert "LLM unavailable" in merged.note


# --- loss runs and schedule count ---


def _inventory() -> list[AttachmentEntry]:
    return [AttachmentEntry(name=PDF, kind="pdf", readable=True, note="1 page(s)", doc_type="application")]


def test_loss_runs_come_from_the_parser_result():
    found = merge_loss_runs(LossRunsResult(present=True, reason="r", source_file="lr.pdf", locator="PDF page 1",
                                           excerpt="Loss runs report", inventory=_inventory()))
    assert (found.state, found.value) == ("KNOWN", True)
    absent = merge_loss_runs(LossRunsResult(present=False, reason="none", locator="uploaded attachment inventory",
                                            inventory=_inventory()))
    assert (absent.state, absent.value) == ("KNOWN", False)
    assert absent.checked_sources == [PDF]
    unreadable = merge_loss_runs(LossRunsResult(present=None, reason="scan.pdf unreadable", inventory=_inventory()))
    assert (unreadable.state, unreadable.value) == ("INVALID", None)


def test_schedule_count_from_csv():
    known = merge_schedule_count(CsvCount(name="vehicles.csv", row_count=0, locator="CSV header only, no data rows"))
    assert (known.state, known.value) == ("KNOWN", 0)
    bad = merge_schedule_count(CsvCount(name="vehicles.csv", error="CSV is empty"))
    assert (bad.state, bad.value) == ("INVALID", None)
    assert merge_schedule_count(None).state == "MISSING"


def test_merge_all_returns_all_nine_fields():
    merged = merge_all([_source(EMAIL)], LossRunsResult(present=False, reason="none"), None)
    assert set(merged) == set(LLM_FIELDS) | {"loss_runs_present", "schedule_vehicle_count"}


# --- more combining cases ---


def test_one_valid_and_one_invalid_answer_is_invalid_with_both_kept():
    merged = merge_field("operating_radius_miles", [_source(EMAIL, operating_radius_miles="150"),
                                                    _source(PDF, "application", operating_radius_miles="about 150")])
    assert merged.state == "INVALID"
    assert merged.value is None
    assert {e.source_file for e in merged.evidence} == {EMAIL, PDF}


def test_not_provided_in_one_source_and_stated_in_the_other_is_known():
    merged = merge_field("requested_effective_date", [_source(EMAIL, requested_effective_date=None),
                                                      _source(PDF, "application", requested_effective_date="2026-11-01")])
    assert (merged.state, merged.value) == ("KNOWN", "2026-11-01")


def test_business_name_conflict():
    merged = merge_field("business_name", [_source(EMAIL, business_name="Alpha LLC"),
                                           _source(PDF, "application", business_name="Beta LLC")])
    assert merged.state == "CONFLICT"
    assert merged.value is None


def test_every_source_failed_is_missing_with_nothing_checked():
    failed = [SourceExtraction(source_file=EMAIL, source_kind="email", error="LLM unavailable: x"),
              SourceExtraction(source_file=PDF, source_kind="application", error="LLM unavailable: x")]
    merged = merge_field("business_name", failed)
    assert merged.state == "MISSING"
    assert merged.checked_sources == []
    assert "LLM unavailable" in merged.note


def test_no_sources_at_all_is_missing():
    merged = merge_field("business_name", [])
    assert merged.state == "MISSING"
    assert merged.checked_sources == []
