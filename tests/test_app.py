"""The Streamlit screen, run headless with Streamlit's AppTest. LLM calls are stubbed.
(AppTest cannot upload files, so these tests use the paste-email box.)"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import extract, narrative
from app.models import LLM_FIELDS, LlmExtraction, LlmField, LlmNarrative

APP = str(Path(__file__).parent.parent / "streamlit_app.py")
EMAIL = (Path(__file__).parent.parent / "sample_submissions" / "SUB-C" / "broker_email.txt").read_text()

STATED = {
    "business_name": ("Metro Route Example LLC", "Business name: Metro Route Example LLC"),
    "requested_effective_date": ("2026-11-01", "Requested effective date: 2026-11-01"),
    "declared_vehicle_count": ("4", "Declared vehicle count: 4"),
    "operating_radius_miles": ("180", "Operating radius miles: 180"),
    "hazardous_materials": ("Yes", "Hazardous materials: Yes"),
}


def fake_extraction(*args) -> LlmExtraction:
    fields = {name: LlmField(value_text=None, excerpt=None) for name in LLM_FIELDS}
    for name, (value, excerpt) in STATED.items():
        fields[name] = LlmField(value_text=value, excerpt=excerpt)
    return LlmExtraction(**fields)


@pytest.fixture
def stub_llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(extract, "call_llm", fake_extraction)
    monkeypatch.setattr(narrative, "call_llm", lambda *a: LlmNarrative(summary="Test summary", email_draft="Test draft"))


def processed_app() -> AppTest:
    at = AppTest.from_file(APP).run(timeout=30)
    at.text_area[0].set_value(EMAIL)
    at.button[0].click().run(timeout=30)  # Process packet
    return at


def test_starts_with_an_empty_screen(stub_llm):
    at = AppTest.from_file(APP).run(timeout=30)
    assert not at.exception
    assert [h.value for h in at.header] == []


def test_processing_nothing_shows_an_error(stub_llm):
    at = AppTest.from_file(APP).run(timeout=30)
    at.button[0].click().run(timeout=30)
    assert [e.value for e in at.error] == ["Upload the packet's files or paste the broker email first."]


def test_pasted_email_is_processed_and_all_sections_shown(stub_llm):
    at = processed_app()
    headers = [h.value for h in at.header]
    assert not at.exception
    assert [s.value for s in at.success] == ["Packet processed."]
    assert headers[0] == "Overall: NEEDS_REVIEW"
    assert any(m.value.startswith("**Needs attention:**") for m in at.markdown)
    for section in ("Extracted fields", "Attachment inventory", "Correct a field",
                    "Underwriter summary (AI-generated)", "Draft broker email"):
        assert section in headers
    assert "Test summary" in [m.value for m in at.markdown]
    assert at.text_area[1].value == "Test draft"  # editable draft


def test_llm_unavailable_is_shown_and_rules_still_displayed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # empty: treated as missing, and .env does not override it
    at = processed_app()
    errors = [e.value for e in at.error]
    assert not at.exception
    assert any("LLM unavailable" in e for e in errors)
    assert [h.value for h in at.header][0] == "Overall: NEEDS_REVIEW"
    assert len(at.dataframe) == 2  # rules table and attachment inventory


def test_correction_is_labelled_and_rules_rerun(stub_llm):
    at = processed_app()
    at.selectbox[0].set_value("hazardous_materials")
    at.text_input[0].set_value("No")
    at.button(key="FormSubmitter:override_form-Apply correction and rerun checks").click().run(timeout=30)
    labels = [x.label for x in at.expander]
    assert "hazardous_materials: No [KNOWN] - HUMAN OVERRIDE" in labels
    rules = at.dataframe[0].value
    assert rules.loc[rules["Rule"] == "R4", "Status"].item() == "PASS"


def test_summary_is_marked_stale_after_a_correction_and_can_be_regenerated(stub_llm, monkeypatch):
    at = processed_app()
    assert not at.warning
    at.selectbox[0].set_value("hazardous_materials")
    at.text_input[0].set_value("No")
    at.button(key="FormSubmitter:override_form-Apply correction and rerun checks").click().run(timeout=30)
    assert any("before your latest corrections" in w.value for w in at.warning)

    seen = []
    def fake_narrative(prompt):
        seen.append(prompt)
        return LlmNarrative(summary="New summary", email_draft="New draft")
    monkeypatch.setattr(narrative, "call_llm", fake_narrative)
    next(b for b in at.button if b.label == "Regenerate summary and email").click().run(timeout=30)

    assert "hazardous_materials: False [KNOWN] Human override" in seen[0]
    assert not any("before your latest corrections" in w.value for w in at.warning)
    assert "New summary" in [m.value for m in at.markdown]
    assert at.text_area[1].value == "New draft"


def test_invalid_correction_is_rejected(stub_llm):
    at = processed_app()
    at.selectbox[0].set_value("hazardous_materials")
    at.text_input[0].set_value("maybe")
    at.button(key="FormSubmitter:override_form-Apply correction and rerun checks").click().run(timeout=30)
    assert any(e.value.startswith("Not applied") for e in at.error)
    assert not any("HUMAN OVERRIDE" in x.label for x in at.expander)


def test_start_new_packet_clears_everything(stub_llm):
    at = processed_app()
    at.button[1].click().run(timeout=30)  # Start new packet
    assert [h.value for h in at.header] == []
    assert at.text_area[0].value == ""
    for key in ("merged", "extractions", "inventory", "narrative", "overrides"):
        assert key not in at.session_state
