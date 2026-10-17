from pathlib import Path

from app.models import AttachmentEntry, PdfDocument, PdfPage, UploadedFile
from app.parsers import (
    PASTED_EMAIL_NAME,
    build_inventory,
    classify_file,
    classify_pdf,
    count_csv_rows,
    detect_loss_runs,
    read_pdf,
    sort_files,
)


def test_known_extensions_are_classified():
    assert classify_file("application.pdf") == "pdf"
    assert classify_file("vehicles.csv") == "csv"
    assert classify_file("broker_email.txt") == "email"


def test_extension_is_case_insensitive():
    assert classify_file("APPLICATION.PDF") == "pdf"
    assert classify_file("Vehicles.Csv") == "csv"


def test_unknown_or_missing_extension_is_unsupported():
    assert classify_file("photo.png") == "unsupported"
    assert classify_file("notes.docx") == "unsupported"
    assert classify_file("README") == "unsupported"


def test_unsupported_files_are_kept_not_dropped():
    result = sort_files([("application.pdf", b"x"), ("photo.png", b"y")])
    assert [(f.name, f.kind) for f in result] == [("application.pdf", "pdf"), ("photo.png", "unsupported")]


def test_pasted_email_is_added_as_email():
    result = sort_files([("vehicles.csv", b"x")], pasted_email="Business name: Example LLC")
    assert result[-1].name == PASTED_EMAIL_NAME
    assert result[-1].kind == "email"
    assert result[-1].data == b"Business name: Example LLC"


def test_blank_pasted_email_is_ignored():
    assert sort_files([], pasted_email="   \n ") == []


def test_pdf_text_is_read_per_page_with_page_numbers():
    path = Path(__file__).parent.parent / "sample_submissions" / "SUB-A" / "application.pdf"
    doc = read_pdf(path.name, path.read_bytes())
    assert doc.readable
    assert doc.pages[0].page_number == 1
    assert "Business name" in doc.pages[0].text


def test_corrupt_pdf_is_unreadable_not_a_crash():
    doc = read_pdf("broken.pdf", b"this is not a pdf")
    assert not doc.readable
    assert doc.pages == []
    assert "could not be opened" in doc.error


HEADER = "vehicle_id,vin,year,make,model\n"


def test_csv_counts_data_rows_and_excludes_header():
    data = (HEADER + "A-01,V1,2022,M,Van\nA-02,V2,2023,M,Van\nA-03,V3,2024,M,Van\n").encode()
    result = count_csv_rows("vehicles.csv", data)
    assert result.row_count == 3
    assert result.locator == "CSV data rows 1-3 (header excluded)"
    assert result.error is None


def test_csv_blank_lines_are_not_counted():
    data = (HEADER + "A-01,V1,2022,M,Van\n\n   \n,,,,\nA-02,V2,2023,M,Van\n\n").encode()
    assert count_csv_rows("vehicles.csv", data).row_count == 2


def test_csv_duplicate_rows_are_counted_without_deduplication():
    data = (HEADER + "A-01,V1,2022,M,Van\nA-01,V1,2022,M,Van\n").encode()
    assert count_csv_rows("vehicles.csv", data).row_count == 2


def test_csv_header_only_is_zero_vehicles():
    result = count_csv_rows("vehicles.csv", HEADER.encode())
    assert result.row_count == 0
    assert result.error is None


def test_empty_csv_cannot_be_parsed():
    result = count_csv_rows("vehicles.csv", b"")
    assert result.row_count is None
    assert result.error == "CSV is empty"


def test_non_text_csv_cannot_be_parsed():
    result = count_csv_rows("vehicles.csv", b"\xff\xfe\x00\x81")
    assert result.row_count is None
    assert "could not be parsed" in result.error


def test_inventory_lists_every_file_with_readability():
    files = sort_files(
        [
            ("vehicles.csv", (HEADER + "A-01,V1,2022,M,Van\n").encode()),
            ("broken.pdf", b"not a pdf"),
            ("photo.png", b"\x89PNG"),
        ],
        pasted_email="Business name: Example LLC",
    )
    inventory = build_inventory(files)
    assert [(e.name, e.kind, e.readable) for e in inventory] == [
        ("vehicles.csv", "csv", True),
        ("broken.pdf", "pdf", False),
        ("photo.png", "unsupported", False),
        (PASTED_EMAIL_NAME, "email", True),
    ]
    assert inventory[0].note == "1 data row(s)"
    assert inventory[2].note == "unsupported file type, not read"
    assert [e.doc_type for e in inventory] == ["vehicle_schedule", "unreadable", "unsupported", "email"]


# --- PDF classification and loss runs detection ---

SAMPLES = Path(__file__).parent.parent / "sample_submissions"


def _text_pdf(name: str, text: str) -> PdfDocument:
    return PdfDocument(name=name, pages=[PdfPage(page_number=1, text=text)])


def _sample_files(folder: str) -> list[UploadedFile]:
    paths = sorted((SAMPLES / folder).iterdir())
    return sort_files([(p.name, p.read_bytes()) for p in paths])


def test_classify_pdf_uses_heading_text_not_filename():
    doc = _text_pdf("scan_0042.pdf", "Fictional data\n1\nLoss runs report\nReported claims\n0")
    assert classify_pdf(doc) == ("loss_runs", "Loss runs report")


def test_classify_pdf_recognises_loss_history_and_application():
    assert classify_pdf(_text_pdf("x.pdf", "Loss History Summary\n..."))[0] == "loss_runs"
    assert classify_pdf(_text_pdf("x.pdf", "Commercial auto application\nBusiness name"))[0] == "application"


def test_loss_runs_phrase_in_application_body_does_not_count():
    text = "Commercial auto application\nBusiness name\nA\nB\nC\nD\nLoss runs attached?\nYes"
    assert classify_pdf(_text_pdf("x.pdf", text))[0] == "application"


def test_classify_pdf_other_and_unreadable():
    assert classify_pdf(_text_pdf("x.pdf", "Driver list\nName"))[0] == "other"
    assert classify_pdf(PdfDocument(name="x.pdf", error="PDF has no selectable text"))[0] == "unreadable"


def test_sample_pdfs_are_classified_by_content():
    inventory = build_inventory(_sample_files("SUB-A"))
    types = {e.name: e.doc_type for e in inventory}
    assert types["application.pdf"] == "application"
    assert types["loss_runs.pdf"] == "loss_runs"


def test_readable_zero_claim_report_counts_as_present():
    result = detect_loss_runs(build_inventory(_sample_files("SUB-A")))
    assert result.present is True
    assert result.source_file == "loss_runs.pdf"
    assert result.locator == "PDF page 1"
    assert result.excerpt == "Loss runs report"


def test_email_promise_only_is_not_a_report():
    # SUB-B's email says loss runs will be sent separately; no report file is supplied.
    result = detect_loss_runs(build_inventory(_sample_files("SUB-B")))
    assert result.present is False
    assert result.locator == "uploaded attachment inventory"
    assert len(result.inventory) == 3


def _entry(name: str, doc_type: str, readable: bool = True) -> AttachmentEntry:
    kind = "unsupported" if doc_type == "unsupported" else "pdf"
    return AttachmentEntry(name=name, kind=kind, readable=readable, note="n", doc_type=doc_type)


def test_no_report_is_false():
    assert detect_loss_runs([_entry("application.pdf", "application")]).present is False


def test_readable_other_pdf_is_not_a_potential_report():
    result = detect_loss_runs([_entry("application.pdf", "application"), _entry("drivers.pdf", "other")])
    assert result.present is False


def test_unreadable_pdf_makes_loss_runs_unknown():
    result = detect_loss_runs([_entry("application.pdf", "application"), _entry("scan.pdf", "unreadable", False)])
    assert result.present is None
    assert "scan.pdf" in result.reason


def test_unsupported_file_makes_loss_runs_unknown():
    result = detect_loss_runs([_entry("losses.docx", "unsupported", False)])
    assert result.present is None


def test_readable_report_wins_over_unreadable_file():
    result = detect_loss_runs([_entry("scan.pdf", "unreadable", False), _entry("lr.pdf", "loss_runs")])
    assert result.present is True
