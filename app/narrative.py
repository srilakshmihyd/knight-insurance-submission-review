import os

import anthropic

from app.extract import get_model
from app.models import Evaluation, LlmNarrative, MergedField, Narrative

SYSTEM_PROMPT = """You help a commercial auto underwriter review a submission.

You are given the extracted fields, the results of five checks (already decided by code), and the
list of items to request from the broker.

Write two things:
1. summary: a concise summary for the underwriter (at most 120 words). State the overall status,
   which checks did not pass and why, and any source conflicts with both values. Use only the
   facts given. Do not change any check result.
2. email_draft: a polite, short email to the broker requesting each item in "Items to request".
   Name each item clearly. For a conflict, quote both values and ask which is correct.
   If there are no items, write a short note confirming the submission is complete for review.
   Do not say coverage is approved, declined, quoted, priced or bound. Sign off as
   "Underwriting team". Plain text only."""


def _show(value) -> str:
    return "not available" if value is None else str(value)


def items_to_request(fields: dict[str, MergedField], evaluation: Evaluation) -> list[str]:
    """Decided in code: every field that is not KNOWN, plus every FLAG rule whose inputs are all
    KNOWN (e.g. counts differ, radius over 150, hazardous Yes, no loss runs attached)."""
    items = []
    for field in fields.values():
        if field.state == "MISSING":
            items.append(f"{field.name}: missing")
        elif field.state == "CONFLICT":
            answers = ", ".join(f"{e.source_file} says '{e.value_text}'" for e in field.evidence)
            items.append(f"{field.name}: sources disagree ({answers})")
        elif field.state == "INVALID":
            items.append(f"{field.name}: unclear or invalid ({field.note})")
    for rule in evaluation.rules:
        if rule.status == "FLAG" and all(s == "KNOWN" for s in rule.states.values()):
            items.append(f"{rule.rule_id} {rule.name}: {rule.reason}")
    return items


def build_prompt(fields: dict[str, MergedField], evaluation: Evaluation) -> str:
    """Plain-text facts for the LLM."""
    lines = ["Fields:"]
    for field in fields.values():
        lines.append(f"- {field.name}: {_show(field.value)} [{field.state}] {field.note}".rstrip())
    lines.append("")
    lines.append(f"Checks (overall {evaluation.overall}):")
    for rule in evaluation.rules:
        lines.append(f"- {rule.rule_id} {rule.name}: {rule.status} - {rule.reason}")
    lines.append("")
    lines.append("Items to request:")
    items = items_to_request(fields, evaluation)
    lines.extend(f"- {item}" for item in items) if items else lines.append("- none")
    return "\n".join(lines)


def call_llm(prompt: str) -> LlmNarrative:
    """One structured-output call. Raises on any problem."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.parse(
        model=get_model(),
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        output_format=LlmNarrative,
    )
    if response.parsed_output is None:
        raise RuntimeError("the model's answer did not match the expected JSON schema")
    return response.parsed_output


def write_narrative(fields: dict[str, MergedField], evaluation: Evaluation) -> Narrative:
    """Summary + draft email. Never raises; rule results are untouched either way.
    The email is only a draft for the user to edit; nothing is sent."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Narrative(error="LLM unavailable: ANTHROPIC_API_KEY is not set")
    try:
        result = call_llm(build_prompt(fields, evaluation))
    except Exception as exc:
        return Narrative(error=f"LLM unavailable: {exc}")
    return Narrative(summary=result.summary, email_draft=result.email_draft)
