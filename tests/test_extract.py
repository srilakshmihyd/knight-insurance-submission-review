from pathlib import Path

import pytest

from app import extract
from app.extract import extract_source, extraction_sources, find_locator
from app.models import LLM_FIELDS, LlmExtraction, LlmField
from app.parsers import build_inventory, sort_files

SAMPLES = Path(__file__).parent.parent / "sample_submissions"


def _fake_result(**answers: tuple[str | None, str | None]) -> LlmExtraction:
    """An LlmExtraction where unlisted fields are not mentioned (both null)."""
    fields = {name: LlmField(value_text=None, excerpt=None) for name in LLM_FIELDS}
    for name, (value, excerpt) in answers.items():
        fields[name] = LlmField(value_text=value, excerpt=excerpt)
    return LlmExtraction(**fields)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


def test_missing_key_is_llm_unavailable_without_calling(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(extract, "call_llm", lambda *a: pytest.fail("must not call the LLM"))
    result = extract_source("broker_email.txt", "email", ["Business name: X"])
    assert result.error == "LLM unavailable: ANTHROPIC_API_KEY is not set"
    assert result.fields == []


def test_llm_failure_is_reported_not_raised(with_key, monkeypatch):
    def boom(*a):
        raise RuntimeError("the model's answer was cut off before it finished")

    monkeypatch.setattr(extract, "call_llm", boom)
    result = extract_source("broker_email.txt", "email", ["Business name: X"])
    assert result.error.startswith("LLM unavailable:")
    assert result.fields == []


def test_locator_is_found_across_pdf_line_breaks():
    pages = ["Commercial auto application", "Field\nOperating radius miles\n150\nHazardous materials\nNo"]
    assert find_locator("Operating radius miles 150", "application", pages) == "PDF page 2"
    assert find_locator("Hazardous materials: No", "email", ["Hazardous materials: No"]) == "email body"
    assert find_locator(None, "email", ["anything"]) is None


def test_fields_get_evidence(with_key, monkeypatch):
    email = "Business name: Example LLC\nHazardous materials: Not provided\n"
    fake = _fake_result(
        business_name=("Example LLC", "Business name: Example LLC"),
        hazardous_materials=(None, "Hazardous materials: Not provided"),
    )
    monkeypatch.setattr(extract, "call_llm", lambda *a: fake)
    result = extract_source("broker_email.txt", "email", [email])
    by_name = {f.field: f for f in result.fields}

    assert result.error is None
    assert [f.field for f in result.fields] == LLM_FIELDS
    assert by_name["business_name"].locator == "email body"
    # "Not provided" -> null value, but the excerpt shows where it was checked
    assert by_name["hazardous_materials"].value_text is None
    assert by_name["hazardous_materials"].locator == "email body"
    # not mentioned at all
    assert by_name["garaging_state"].value_text is None
    assert by_name["garaging_state"].excerpt is None


def test_sources_are_the_email_and_the_application_pdf_only():
    paths = sorted((SAMPLES / "SUB-A").iterdir())
    files = sort_files([(p.name, p.read_bytes()) for p in paths])
    sources = extraction_sources(files, build_inventory(files))
    # loss runs PDF and vehicle CSV are not sent to the LLM
    assert [(name, kind) for name, kind, _ in sources] == [
        ("application.pdf", "application"),
        ("broker_email.txt", "email"),
    ]


def test_pasted_email_is_used_as_the_email_source():
    files = sort_files([], pasted_email="Business name: Pasted LLC")
    sources = extraction_sources(files, build_inventory(files))
    assert [(name, kind) for name, kind, _ in sources] == [("pasted broker email", "email")]


def test_excerpt_not_in_source_has_no_location():
    assert find_locator("Operating radius miles 999", "email", ["Operating radius miles: 150"]) is None


def test_unreadable_application_pdf_is_not_sent():
    files = sort_files([("application.pdf", b"not a pdf"), ("broker_email.txt", b"Business name: X")])
    sources = extraction_sources(files, build_inventory(files))
    assert [(name, kind) for name, kind, _ in sources] == [("broker_email.txt", "email")]


def test_packet_with_no_email_or_application_has_no_llm_sources():
    files = sort_files([("vehicles.csv", b"vehicle_id,vin,year,make,model\nA-01,V1,2022,M,Van\n")])
    assert extraction_sources(files, build_inventory(files)) == []
