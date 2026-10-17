import csv
from io import BytesIO, StringIO
from pathlib import Path

from pypdf import PdfReader

from app.models import (
    AttachmentEntry,
    CsvCount,
    FileKind,
    LossRunsResult,
    PdfDocType,
    PdfDocument,
    PdfPage,
    UploadedFile,
)

# PDF classification looks only at the first few nonblank lines of page 1 (the document title area),
# so a field label like "Loss runs attached?" inside an application body does not count.
HEADING_LINES = 5
# Common titles for a loss runs report. Each names a loss or claims record, not just the word "claims".
LOSS_RUNS_PHRASES = (
    "loss run",
    "loss history",
    "loss experience",
    "loss report",
    "loss summary",
    "claims history",
    "claim history",
    "claims experience",
    "claims report",
    "claims summary",
)
APPLICATION_PHRASE = "application"

EXTENSION_KINDS: dict[str, FileKind] = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".txt": "email",
}

PASTED_EMAIL_NAME = "pasted broker email"


def classify_file(name: str) -> FileKind:
    """Decide a file's kind from its extension only. Unknown extensions are 'unsupported'."""
    return EXTENSION_KINDS.get(Path(name).suffix.lower(), "unsupported")


def sort_files(files: list[tuple[str, bytes]], pasted_email: str = "") -> list[UploadedFile]:
    """Tag every uploaded file with its kind. Nothing is dropped: unsupported files stay in the list.

    Pasted email text, if any, is added as one more email entry.
    """
    sorted_files = [UploadedFile(name=name, data=data, kind=classify_file(name)) for name, data in files]
    if pasted_email.strip():
        sorted_files.append(
            UploadedFile(name=PASTED_EMAIL_NAME, data=pasted_email.encode("utf-8"), kind="email")
        )
    return sorted_files


def read_pdf(name: str, data: bytes) -> PdfDocument:
    """Extract selectable text page by page. A PDF that cannot be opened is returned as
    unreadable with a reason, never raised as an exception."""
    try:
        reader = PdfReader(BytesIO(data))
        pages = [
            PdfPage(page_number=number, text=page.extract_text() or "")
            for number, page in enumerate(reader.pages, start=1)
        ]
    except Exception as exc:
        return PdfDocument(name=name, error=f"PDF could not be opened: {exc}")
    return PdfDocument(name=name, pages=pages)


def count_csv_rows(name: str, data: bytes) -> CsvCount:
    """Count nonblank data rows after the header. No VIN checks or deduplication (per the spec).
    An undecodable or empty file means the CSV cannot be parsed."""
    try:
        rows = list(csv.reader(StringIO(data.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        return CsvCount(name=name, error=f"CSV could not be parsed: {exc}")

    nonblank = [row for row in rows if any(cell.strip() for cell in row)]
    if not nonblank:
        return CsvCount(name=name, error="CSV is empty")

    count = len(nonblank) - 1  # first nonblank row is the header
    if count == 0:
        locator = "CSV header only, no data rows"
    elif count == 1:
        locator = "CSV data row 1 (header excluded)"
    else:
        locator = f"CSV data rows 1-{count} (header excluded)"
    return CsvCount(name=name, row_count=count, locator=locator)


def classify_pdf(doc: PdfDocument) -> tuple[PdfDocType, str]:
    """Decide what a PDF is from the heading lines at the top of page 1 (never from its filename).

    Returns the type and the heading line that decided it. Loss runs is checked first so a
    report titled e.g. "Loss history application summary" is not mistaken for an application.
    """
    if not doc.readable:
        return "unreadable", ""
    first_page = doc.pages[0].text if doc.pages else ""
    lines = [line.strip() for line in first_page.splitlines() if line.strip()]
    heading_lines = lines[:HEADING_LINES]
    for line in heading_lines:
        if any(phrase in line.lower() for phrase in LOSS_RUNS_PHRASES):
            return "loss_runs", line
    for line in heading_lines:
        if APPLICATION_PHRASE in line.lower():
            return "application", line
    return "other", ""


def build_inventory(files: list[UploadedFile]) -> list[AttachmentEntry]:
    """List every supplied file with whether it could be read and what it is. Used as evidence for absent documents."""
    inventory = []
    for f in files:
        if f.kind == "pdf":
            doc = read_pdf(f.name, f.data)
            doc_type, heading = classify_pdf(doc)
            note = f"{len(doc.pages)} page(s)" if doc.readable else doc.error
            inventory.append(
                AttachmentEntry(
                    name=f.name, kind=f.kind, readable=doc.readable, note=note, doc_type=doc_type, heading=heading
                )
            )
        elif f.kind == "csv":
            result = count_csv_rows(f.name, f.data)
            readable = result.error is None
            note = f"{result.row_count} data row(s)" if readable else result.error
            inventory.append(
                AttachmentEntry(name=f.name, kind=f.kind, readable=readable, note=note, doc_type="vehicle_schedule")
            )
        elif f.kind == "email":
            try:
                f.data.decode("utf-8")
                inventory.append(
                    AttachmentEntry(name=f.name, kind=f.kind, readable=True, note="email text", doc_type="email")
                )
            except UnicodeDecodeError:
                inventory.append(
                    AttachmentEntry(
                        name=f.name, kind=f.kind, readable=False, note="email text is not valid UTF-8", doc_type="email"
                    )
                )
        else:
            inventory.append(
                AttachmentEntry(
                    name=f.name,
                    kind=f.kind,
                    readable=False,
                    note="unsupported file type, not read",
                    doc_type="unsupported",
                )
            )
    return inventory


def detect_loss_runs(inventory: list[AttachmentEntry]) -> LossRunsResult:
    """Decide loss_runs_present from the supplied files only. Email text never counts.

    True: a readable PDF is headed as a loss runs / loss history report (zero claims still counts).
    None: no such report, but an unreadable PDF or unsupported file might be one.
    False: no report and nothing that could be one.
    """
    for entry in inventory:
        if entry.doc_type == "loss_runs":
            return LossRunsResult(
                present=True,
                reason=f"{entry.name} is a readable loss runs report",
                source_file=entry.name,
                locator="PDF page 1",
                excerpt=entry.heading,
                inventory=inventory,
            )

    unclassifiable = [e for e in inventory if e.doc_type in ("unreadable", "unsupported")]
    if unclassifiable:
        names = ", ".join(f"{e.name} ({e.note})" for e in unclassifiable)
        return LossRunsResult(
            present=None,
            reason=f"No readable loss runs report; could not read or classify: {names}",
            locator="uploaded attachment inventory",
            inventory=inventory,
        )

    return LossRunsResult(
        present=False,
        reason="No loss runs or loss history report among the uploaded files",
        locator="uploaded attachment inventory",
        inventory=inventory,
    )
