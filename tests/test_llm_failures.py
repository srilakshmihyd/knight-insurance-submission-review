"""LLM failure handling, tested without a live model: the SDK client is replaced with a fake."""

from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2

from app import extract, narrative
from app.extract import extract_all, extract_source
from app.merge import merge_all
from app.narrative import write_narrative
from app.parsers import build_inventory, count_csv_rows, detect_loss_runs, sort_files
from app.rules import evaluate

SAMPLES = Path(__file__).parent.parent / "sample_submissions"


class FakeClient:
    """Stands in for anthropic.Anthropic(). parse() returns `reply` or raises `error`."""

    reply = None
    error = None

    def __init__(self, *args, **kwargs):
        self.messages = self

    def parse(self, **kwargs):
        if FakeClient.error is not None:
            raise FakeClient.error
        return FakeClient.reply


def use_fake_client(monkeypatch, reply=None, error=None):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    FakeClient.reply, FakeClient.error = reply, error
    monkeypatch.setattr(extract.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(narrative.anthropic, "Anthropic", FakeClient)


def run_pipeline(folder: str):
    paths = sorted((SAMPLES / folder).iterdir())
    files = sort_files([(p.name, p.read_bytes()) for p in paths])
    inventory = build_inventory(files)
    extractions = extract_all(files, inventory)
    csv_count = next((count_csv_rows(f.name, f.data) for f in files if f.kind == "csv"), None)
    merged = merge_all(extractions, detect_loss_runs(inventory), csv_count)
    return extractions, merged, evaluate(merged)


def test_answer_not_matching_schema_is_llm_unavailable(monkeypatch):
    use_fake_client(monkeypatch, reply=SimpleNamespace(parsed_output=None))
    result = extract_source("broker_email.txt", "email", ["Business name: X"])
    assert result.error == "LLM unavailable: the model's answer did not match the expected JSON schema"


def test_connection_error_is_llm_unavailable(monkeypatch):
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    use_fake_client(monkeypatch, error=error)
    result = extract_source("broker_email.txt", "email", ["Business name: X"])
    assert result.error.startswith("LLM unavailable:")
    assert result.fields == []


def test_narrative_schema_failure_is_llm_unavailable(monkeypatch):
    use_fake_client(monkeypatch, reply=SimpleNamespace(parsed_output=None))
    _, merged, evaluation = run_pipeline("SUB-A")
    result = write_narrative(merged, evaluation)
    assert result.error.startswith("LLM unavailable:")
    assert result.summary == "" and result.email_draft == ""


def test_llm_down_keeps_deterministic_results(monkeypatch):
    """No key: every LLM source fails, but file-based results and the rules still work."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    extractions, merged, evaluation = run_pipeline("SUB-A")
    status = {r.rule_id: r.status for r in evaluation.rules}

    assert all(e.error and e.error.startswith("LLM unavailable") for e in extractions)
    assert (merged["schedule_vehicle_count"].state, merged["schedule_vehicle_count"].value) == ("KNOWN", 3)
    assert (merged["loss_runs_present"].state, merged["loss_runs_present"].value) == ("KNOWN", True)
    assert status["R5"] == "PASS"  # loss runs come from files, not the LLM
    assert status["R1"] == "UNKNOWN"  # fields could not be read, so not known to be absent
    assert status["R2"] == "UNKNOWN"
    assert evaluation.overall == "NEEDS_REVIEW"


def test_llm_down_still_flags_missing_loss_runs(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _, _, evaluation = run_pipeline("SUB-B")
    assert {r.rule_id: r.status for r in evaluation.rules}["R5"] == "FLAG"
