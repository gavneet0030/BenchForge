"""Bounded local analysis and storage for text-based PDF documents."""

from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_CONFIGURABLE_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_EXTRACTED_TEXT_CHARS = 1_000_000
MAX_PDF_FILENAME_CHARS = 1_024
MAX_SEARCH_TERM_CHARS = 128
MAX_SEARCH_MATCHES = 100
_MAX_STORED_JSON_BYTES = 8 * 1024 * 1024
_TEXT_PREVIEW_CHARS = 2_000


def validate_max_pdf_bytes(max_bytes: int) -> int:
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= MAX_CONFIGURABLE_PDF_BYTES
    ):
        raise ValueError(
            f"PDF upload limit must be between 1 and "
            f"{MAX_CONFIGURABLE_PDF_BYTES:,} bytes."
        )
    return max_bytes


def analyze_pdf_bytes(
    data: bytes,
    filename: str,
    *,
    max_bytes: int = MAX_PDF_BYTES,
) -> dict[str, Any]:
    """Validate a bounded PDF and extract text without performing OCR."""
    validate_max_pdf_bytes(max_bytes)
    if not isinstance(data, bytes):
        raise TypeError("PDF content must be bytes.")
    if not data:
        raise ValueError("The uploaded PDF is empty.")
    if len(data) > max_bytes:
        raise ValueError(f"PDF exceeds the {max_bytes:,}-byte upload limit.")
    if not isinstance(filename, str) or len(filename) > MAX_PDF_FILENAME_CHARS:
        raise ValueError("The PDF filename is invalid.")
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename.lower().endswith(".pdf"):
        raise ValueError("Upload a file with a .pdf extension.")
    if b"%PDF-" not in data[:1_024]:
        raise ValueError("The uploaded file does not have a PDF signature.")

    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported.")
        page_count = len(reader.pages)
        if page_count == 0:
            raise ValueError("The PDF contains no pages.")
        if page_count > MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page analysis limit.")

        pages: list[str] = []
        extracted_chars = 0
        text_truncated = False
        for page in reader.pages:
            separator = "\n\n" if pages else ""
            remaining = MAX_EXTRACTED_TEXT_CHARS - extracted_chars - len(separator)
            if remaining <= 0:
                text_truncated = True
                break
            page_text = page.extract_text() or ""
            if len(page_text) > remaining:
                page_text = page_text[:remaining]
                text_truncated = True
            pages.append(page_text)
            extracted_chars += len(separator) + len(page_text)
            if text_truncated or extracted_chars >= MAX_EXTRACTED_TEXT_CHARS:
                text_truncated = text_truncated or len(pages) < page_count
                break
    except ValueError:
        raise
    except (PdfReadError, OSError, UnicodeError) as exc:
        raise ValueError(f"Unable to read or extract this PDF ({type(exc).__name__}).") from exc

    text = "\n\n".join(pages)
    return {
        "page_count": page_count,
        "status": "text_extracted" if text.strip() else "no_extractable_text",
        "extraction_method": "pypdf text extraction; OCR is not performed",
        "character_count": len(text),
        "word_count": len(text.split()),
        "text_truncated": text_truncated,
        "text_preview": text[:_TEXT_PREVIEW_CHARS],
        "pages": pages,
        "text": text,
    }


def public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if key not in {"pages", "text"}
    }


def store_pdf(
    directory: Path,
    data: bytes,
    filename: str,
    *,
    max_bytes: int = MAX_PDF_BYTES,
) -> dict[str, Any]:
    """Analyze and store a PDF under a generated identifier, never its filename."""
    analysis = analyze_pdf_bytes(data, filename, max_bytes=max_bytes)
    document_id = uuid.uuid4().hex
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise OSError("Document storage must not be a symbolic link.")
    root = root.resolve()
    pdf_path = root / f"{document_id}.pdf"
    metadata_path = root / f"{document_id}.json"
    document = {
        "document_id": document_id,
        "filename": "uploaded.pdf",
        **analysis,
    }
    stored_document = {
        key: value for key, value in document.items() if key != "text"
    }
    created: list[Path] = []
    try:
        with pdf_path.open("xb") as pdf_file:
            created.append(pdf_path)
            pdf_file.write(data)
        with metadata_path.open("x", encoding="utf-8") as metadata_file:
            created.append(metadata_path)
            json.dump(stored_document, metadata_file, ensure_ascii=True, allow_nan=False)
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return public_document(document)


def read_document(directory: Path, document_id: str) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{32}", document_id) is None:
        raise ValueError("Invalid document ID.")
    root = Path(directory).resolve()
    metadata_path = root / f"{document_id}.json"
    if (
        metadata_path.is_symlink()
        or metadata_path.resolve().parent != root
        or not metadata_path.is_file()
    ):
        raise FileNotFoundError("Document not found.")
    if metadata_path.stat().st_size > _MAX_STORED_JSON_BYTES:
        raise ValueError("Stored document analysis exceeds the safety limit.")
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("document_id") != document_id
        or not isinstance(document.get("pages"), list)
        or any(not isinstance(page, str) for page in document["pages"])
    ):
        raise ValueError("Stored document analysis has an invalid shape.")
    document["text"] = "\n\n".join(document["pages"])
    if (
        len(document["text"]) > MAX_EXTRACTED_TEXT_CHARS
        or document.get("character_count") != len(document["text"])
    ):
        raise ValueError("Stored document analysis has invalid text statistics.")
    return document


def search_document(document: dict[str, Any], query: str) -> list[dict[str, Any]]:
    if not isinstance(query, str) or len(query) > MAX_SEARCH_TERM_CHARS:
        raise ValueError(f"Search terms must be at most {MAX_SEARCH_TERM_CHARS} characters.")
    term = query.strip().casefold()
    if not term:
        return []
    matches = []
    for page_number, page_text in enumerate(document["pages"], start=1):
        folded = page_text.casefold()
        offset = 0
        while len(matches) < MAX_SEARCH_MATCHES:
            index = folded.find(term, offset)
            if index < 0:
                break
            start = max(0, index - 80)
            end = min(len(page_text), index + len(term) + 80)
            matches.append(
                {"page": page_number, "snippet": page_text[start:end].strip()}
            )
            offset = index + max(1, len(term))
        if len(matches) >= MAX_SEARCH_MATCHES:
            break
    return matches
