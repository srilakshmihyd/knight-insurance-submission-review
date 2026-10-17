from typing import Literal

from pydantic import BaseModel

FileKind = Literal["pdf", "csv", "email", "unsupported"]


class UploadedFile(BaseModel):
    """One file from the packet (or the pasted email), tagged with its kind."""

    name: str
    data: bytes
    kind: FileKind


class PdfPage(BaseModel):
    """Text of one PDF page. page_number starts at 1, for locators like 'PDF page 1'."""

    page_number: int
    text: str


class PdfDocument(BaseModel):
    """Text of a whole PDF. If it could not be read, pages is empty and error says why."""

    name: str
    pages: list[PdfPage] = []
    error: str | None = None

    @property
    def readable(self) -> bool:
        return self.error is None


class CsvCount(BaseModel):
    """Vehicle schedule row count. If the CSV could not be parsed, row_count is None and error says why."""

    name: str
    row_count: int | None = None
    locator: str = ""
    error: str | None = None


PdfDocType = Literal["application", "loss_runs", "other", "unreadable"]
DocType = Literal["application", "loss_runs", "other", "unreadable", "vehicle_schedule", "email", "unsupported"]


class AttachmentEntry(BaseModel):
    """One line of the attachment inventory: every supplied file, whether it could be read, and a short note.

    doc_type says what the file is. For a classified PDF, heading is the page 1 line that decided it.
    """

    name: str
    kind: FileKind
    readable: bool
    note: str
    doc_type: DocType
    heading: str = ""


LLM_FIELDS = [
    "business_name",
    "requested_effective_date",
    "garaging_state",
    "operations_description",
    "declared_vehicle_count",
    "operating_radius_miles",
    "hazardous_materials",
]


class LlmField(BaseModel):
    """What the LLM found for one field in one source. value_text is the answer exactly as written
    (normalizing is done later in code); excerpt is the source text it came from."""

    value_text: str | None
    excerpt: str | None


class LlmExtraction(BaseModel):
    """The JSON schema the LLM must return for one source document."""

    business_name: LlmField
    requested_effective_date: LlmField
    garaging_state: LlmField
    operations_description: LlmField
    declared_vehicle_count: LlmField
    operating_radius_miles: LlmField
    hazardous_materials: LlmField


class SourceField(BaseModel):
    """One field from one source, with its evidence. locator is worked out in code by finding the
    excerpt in the source text."""

    field: str
    source_file: str
    value_text: str | None
    excerpt: str | None
    locator: str | None


class SourceExtraction(BaseModel):
    """Extraction result for one source (an email or an application PDF). On failure, error says why."""

    source_file: str
    source_kind: Literal["email", "application"]
    fields: list[SourceField] = []
    error: str | None = None


FieldState = Literal["KNOWN", "MISSING", "CONFLICT", "INVALID"]
FieldValue = str | int | float | bool | None


class Evidence(BaseModel):
    """One source's answer for a field: what it said (value_text), the cleaned-up value, and the proof."""

    source_file: str
    locator: str | None
    excerpt: str | None
    value_text: str | None = None
    value: FieldValue = None


class MergedField(BaseModel):
    """The final value for one field after combining sources.

    value is None unless state is KNOWN. evidence keeps every source's answer (both sides of a
    conflict). checked_sources lists the files that were looked at, so MISSING can show them.
    """

    name: str
    state: FieldState
    value: FieldValue = None
    evidence: list[Evidence] = []
    checked_sources: list[str] = []
    note: str = ""
    human_override: bool = False
    original_state: FieldState | None = None
    original_value: FieldValue = None


RuleStatus = Literal["PASS", "FLAG", "UNKNOWN"]


class RuleResult(BaseModel):
    """One rule's outcome. values and states show exactly which field values the rule used."""

    rule_id: str
    name: str
    status: RuleStatus
    reason: str
    values: dict[str, FieldValue]
    states: dict[str, FieldState]


class Evaluation(BaseModel):
    """All five rule results and the overall readiness status (not a coverage decision)."""

    rules: list[RuleResult]
    overall: Literal["READY_FOR_REVIEW", "NEEDS_REVIEW"]


class LlmNarrative(BaseModel):
    """The JSON schema the LLM must return for the summary step."""

    summary: str
    email_draft: str


class Narrative(BaseModel):
    """AI-written underwriter summary and draft broker email. On failure, error says why
    and summary / email_draft are empty."""

    summary: str = ""
    email_draft: str = ""
    error: str | None = None


class LossRunsResult(BaseModel):
    """Whether a loss runs report was actually supplied: True, False, or None when it cannot be determined.

    Evidence is the report's file, page and heading when found; otherwise reason plus the full inventory.
    """

    present: bool | None
    reason: str
    source_file: str | None = None
    locator: str | None = None
    excerpt: str | None = None
    inventory: list[AttachmentEntry] = []
