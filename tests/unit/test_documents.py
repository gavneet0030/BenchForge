import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.errors import PdfReadError

from benchforge import documents


def text_pdf(text="Hello BenchForge local report"):
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 20 100 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def blank_pdf(*, password=None):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if password is not None:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_analyze_text_pdf_extracts_preview_statistics_and_keyword_matches():
    result = documents.analyze_pdf_bytes(text_pdf(), "report.PDF")

    assert result["page_count"] == 1
    assert result["status"] == "text_extracted"
    assert "OCR is not performed" in result["extraction_method"]
    assert result["word_count"] == 4
    assert result["character_count"] == len(result["text"])
    assert "Hello BenchForge" in result["text_preview"]
    stored = {"pages": result["pages"]}
    assert documents.search_document(stored, "benchforge") == [
        {"page": 1, "snippet": "Hello BenchForge local report"}
    ]
    assert documents.search_document(stored, "missing") == []


def test_blank_pdf_reports_no_extractable_text():
    result = documents.analyze_pdf_bytes(blank_pdf(), "scan.pdf")

    assert result["status"] == "no_extractable_text"
    assert result["page_count"] == 1
    assert result["text"] == ""


@pytest.mark.parametrize(
    ("content", "filename", "message"),
    [
        pytest.param(b"", "empty.pdf", "empty", id="empty"),
        pytest.param(b"not a pdf", "report.pdf", "signature", id="signature"),
        pytest.param(text_pdf(), "report.txt", "extension", id="extension"),
        pytest.param(
            b"x" * (documents.MAX_PDF_BYTES + 1),
            "large.pdf",
            "upload limit",
            id="size-limit",
        ),
        pytest.param(
            b"%PDF-1.4\ntruncated",
            "broken.pdf",
            "Unable to read",
            id="malformed",
        ),
    ],
)
def test_pdf_validation_rejects_invalid_inputs(content, filename, message):
    with pytest.raises(ValueError, match=message):
        documents.analyze_pdf_bytes(content, filename)


def test_encrypted_pdf_is_rejected():
    with pytest.raises(ValueError, match="Encrypted"):
        documents.analyze_pdf_bytes(blank_pdf(password="test"), "locked.pdf")


def test_page_count_limit_is_enforced():
    writer = PdfWriter()
    for _ in range(documents.MAX_PDF_PAGES + 1):
        writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)

    with pytest.raises(ValueError, match="200-page"):
        documents.analyze_pdf_bytes(output.getvalue(), "many-pages.pdf")


def test_extraction_errors_are_reported(monkeypatch):
    class BrokenPage:
        def extract_text(self):
            raise PdfReadError("invalid content stream")

    class BrokenReader:
        is_encrypted = False

        def __init__(self):
            self.pages = [BrokenPage()]

    monkeypatch.setattr(documents, "PdfReader", lambda *_args, **_kwargs: BrokenReader())

    with pytest.raises(ValueError, match="Unable to read or extract this PDF"):
        documents.analyze_pdf_bytes(text_pdf(), "broken-content.pdf")


def test_upload_filename_never_becomes_a_storage_path(tmp_path):
    directory = tmp_path / "documents"
    result = documents.store_pdf(directory, text_pdf(), r"..\outside.pdf")

    assert result["filename"] == "uploaded.pdf"
    assert len(result["document_id"]) == 32
    assert sorted(path.suffix for path in directory.iterdir()) == [".json", ".pdf"]
    stored = documents.read_document(directory, result["document_id"])
    assert "Hello BenchForge" in stored["text"]
    assert not (tmp_path / "outside.pdf").exists()


def test_document_ids_and_search_terms_are_bounded(tmp_path):
    with pytest.raises(ValueError, match="Invalid document ID"):
        documents.read_document(tmp_path, "..\\outside")
    with pytest.raises(ValueError, match="at most"):
        documents.search_document(
            {"pages": []}, "x" * (documents.MAX_SEARCH_TERM_CHARS + 1)
        )

def test_store_pdf_removes_pdf_when_metadata_write_fails(tmp_path, monkeypatch):
    directory = tmp_path / "documents"
    original_open = Path.open

    def fail_metadata_open(self, mode="r", *args, **kwargs):
        if self.suffix == ".json" and mode == "x":
            raise OSError("simulated metadata storage failure")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_metadata_open)

    with pytest.raises(OSError, match="simulated metadata storage failure"):
        documents.store_pdf(directory, text_pdf(), "report.pdf")

    assert directory.is_dir()
    assert list(directory.iterdir()) == []



def test_read_document_rejects_corrupted_metadata_json(tmp_path):
    result = documents.store_pdf(tmp_path, text_pdf(), "report.pdf")
    metadata_path = tmp_path / f"{result['document_id']}.json"

    metadata_path.write_text("{invalid-json", encoding="utf-8")

    with pytest.raises(ValueError):
        documents.read_document(tmp_path, result["document_id"])


def test_read_document_rejects_invalid_metadata_shape(tmp_path):
    result = documents.store_pdf(tmp_path, text_pdf(), "report.pdf")
    metadata_path = tmp_path / f"{result['document_id']}.json"

    metadata_path.write_text(
        json.dumps(
            {
                "document_id": result["document_id"],
                "pages": "this-must-be-a-list",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid shape"):
        documents.read_document(tmp_path, result["document_id"])


def test_read_document_rejects_inconsistent_character_count(tmp_path):
    result = documents.store_pdf(tmp_path, text_pdf(), "report.pdf")
    metadata_path = tmp_path / f"{result['document_id']}.json"

    stored = documents.read_document(tmp_path, result["document_id"])
    stored["character_count"] = len(stored["text"]) + 1
    stored.pop("text", None)

    metadata_path.write_text(
        json.dumps(stored),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid text statistics"):
        documents.read_document(tmp_path, result["document_id"])


def test_read_document_rejects_oversized_metadata(tmp_path, monkeypatch):
    result = documents.store_pdf(tmp_path, text_pdf(), "report.pdf")
    metadata_path = tmp_path / f"{result['document_id']}.json"

    monkeypatch.setattr(documents, "_MAX_STORED_JSON_BYTES", 32)
    metadata_path.write_text(" " * 64, encoding="utf-8")

    with pytest.raises(ValueError, match="safety limit"):
        documents.read_document(tmp_path, result["document_id"])
