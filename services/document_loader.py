from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from pypdf import PdfReader
from pypdf.errors import PdfReadError


SUPPORTED_DOCUMENT_SUFFIXES = {".pdf", ".txt", ".docx", ".md"}


class DocumentLoadError(ValueError):
    """Raised when an uploaded document cannot be read or contains no text."""


@dataclass(frozen=True)
class UploadedDocument:
    filename: str
    content: bytes


def extract_document_text(document: UploadedDocument) -> str:
    suffix = Path(document.filename).suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_SUFFIXES:
        raise DocumentLoadError(
            f"Unsupported document format for {document.filename!r}."
        )
    if not document.content:
        raise DocumentLoadError(f"{document.filename!r} is empty.")

    try:
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(document.content))
            if reader.is_encrypted:
                raise DocumentLoadError(
                    f"{document.filename!r} is password-protected."
                )
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix == ".docx":
            source = Document(BytesIO(document.content))
            paragraphs = [paragraph.text for paragraph in source.paragraphs]
            tables = [
                "\n".join(
                    " | ".join(cell.text for cell in row.cells)
                    for row in table.rows
                )
                for table in source.tables
            ]
            text = "\n".join(paragraphs + tables)
        else:
            text = document.content.decode("utf-8-sig")
    except DocumentLoadError:
        raise
    except (
        UnicodeDecodeError,
        ValueError,
        zipfile.BadZipFile,
        PackageNotFoundError,
        PdfReadError,
    ) as exc:
        raise DocumentLoadError(
            f"Could not read {document.filename!r} as a valid {suffix[1:].upper()} file."
        ) from exc

    normalized_text = re.sub(r"\s+", " ", text).strip()
    if not normalized_text:
        raise DocumentLoadError(
            f"No extractable text was found in {document.filename!r}."
        )
    return normalized_text


def split_text(
    text: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
) -> list[str]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be greater than zero.")
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size.")

    normalized_text = re.sub(r"\s+", " ", text).strip()
    if not normalized_text:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized_text):
        end = min(start + chunk_size, len(normalized_text))
        if end < len(normalized_text):
            boundary = normalized_text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunk = normalized_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(normalized_text):
            break
        start = max(start + 1, end - chunk_overlap)
    return chunks
