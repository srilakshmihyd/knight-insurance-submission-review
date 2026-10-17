import pytest

from app import narrative
from app.models import Evidence, LlmNarrative, MergedField
from app.narrative import build_prompt, items_to_request, write_narrative
from app.rules import evaluate

CHECKED = ["broker_email.txt", "application.pdf"]


def known(name: str, value) -> MergedField:
    return MergedField(name=name, state="KNOWN", value=value, checked_sources=CHECKED)


def sub_c_like_fields() -> dict[str, MergedField]:
    """Radius conflict, counts differ, no loss runs; everything else fine."""
    radius = MergedField(
        name="operating_radius_miles",
        state="CONFLICT",
        checked_sources=CHECKED,
        evidence=[
            Evidence(source_file="broker_email.txt", locator="email body", excerpt="radius: 180", value_text="180", value=180),
            Evidence(source_file="application.pdf", locator="PDF page 1", excerpt="radius 80", value_text="80", value=80),
        ],
    )
    return {
        "business_name": known("business_name", "Example LLC"),
        "requested_effective_date": known("requested_effective_date", "2026-11-01"),
        "garaging_state": known("garaging_state", "CA"),
        "operations_description": known("operations_description", "Local delivery"),
        "declared_vehicle_count": known("declared_vehicle_count", 4),
        "operating_radius_miles": radius,
        "hazardous_materials": known("hazardous_materials", False),
        "loss_runs_present": known("loss_runs_present", False),
        "schedule_vehicle_count": known("schedule_vehicle_count", 3),
    }


def test_items_to_request_cover_conflicts_and_flags():
    fields = sub_c_like_fields()
    items = items_to_request(fields, evaluate(fields))
    assert any("operating_radius_miles" in i and "'180'" in i and "'80'" in i for i in items)
    assert any(i.startswith("R2") for i in items)  # counts differ
    assert any(i.startswith("R5") for i in items)  # no loss runs
    assert not any(i.startswith("R3") for i in items)  # R3 is UNKNOWN from the conflict, already listed


def test_missing_field_is_requested():
    fields = sub_c_like_fields()
    fields["requested_effective_date"] = MergedField(name="requested_effective_date", state="MISSING",
                                                     checked_sources=CHECKED)
    items = items_to_request(fields, evaluate(fields))
    assert "requested_effective_date: missing" in items


def test_prompt_contains_rule_results():
    fields = sub_c_like_fields()
    prompt = build_prompt(fields, evaluate(fields))
    assert "R2 Vehicle count reconciliation: FLAG" in prompt
    assert "overall NEEDS_REVIEW" in prompt


def test_missing_key_is_llm_unavailable_without_calling(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(narrative, "call_llm", lambda *a: pytest.fail("must not call the LLM"))
    fields = sub_c_like_fields()
    result = write_narrative(fields, evaluate(fields))
    assert result.error == "LLM unavailable: ANTHROPIC_API_KEY is not set"
    assert result.summary == "" and result.email_draft == ""


def test_llm_failure_is_reported_and_rules_are_unchanged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    def boom(*a):
        raise RuntimeError("the model declined to write the summary")

    monkeypatch.setattr(narrative, "call_llm", boom)
    fields = sub_c_like_fields()
    evaluation = evaluate(fields)
    before = evaluation.model_dump()
    result = write_narrative(fields, evaluation)
    assert result.error.startswith("LLM unavailable:")
    assert evaluation.model_dump() == before


def test_successful_call_returns_summary_and_draft(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(narrative, "call_llm", lambda *a: LlmNarrative(summary="S", email_draft="E"))
    fields = sub_c_like_fields()
    result = write_narrative(fields, evaluate(fields))
    assert (result.summary, result.email_draft, result.error) == ("S", "E", None)


def test_nothing_to_request_says_none():
    fields = sub_c_like_fields()
    fields["operating_radius_miles"] = known("operating_radius_miles", 80)
    fields["schedule_vehicle_count"] = known("schedule_vehicle_count", 4)
    fields["loss_runs_present"] = known("loss_runs_present", True)
    evaluation = evaluate(fields)
    assert items_to_request(fields, evaluation) == []
    assert "Items to request:\n- none" in build_prompt(fields, evaluation)


def test_invalid_field_is_requested():
    fields = sub_c_like_fields()
    fields["hazardous_materials"] = MergedField(name="hazardous_materials", state="INVALID", checked_sources=CHECKED,
                                                note="broker_email.txt: 'maybe' is not an explicit Yes or No.")
    items = items_to_request(fields, evaluate(fields))
    assert any(i.startswith("hazardous_materials: unclear or invalid") for i in items)
