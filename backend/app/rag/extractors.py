"""Text extraction for supported document types.

Each extractor returns a list of `Section(title, text)` so chunking can respect
document structure (headings, CSV rows, JSON keys).
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass

SUPPORTED_TYPES = {"pdf", "docx", "txt", "md", "csv", "json"}
EXTENSION_MAP = {"markdown": "md", "text": "txt"}


class ExtractionError(Exception):
    pass


@dataclass
class Section:
    title: str | None
    text: str


def detect_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext = EXTENSION_MAP.get(ext, ext)
    if ext not in SUPPORTED_TYPES:
        raise ExtractionError(
            f"Unsupported file type '.{ext}'. Supported: {', '.join(sorted(SUPPORTED_TYPES))}."
        )
    return ext


def validate_magic(doc_type: str, data: bytes) -> None:
    """Cheap content sniffing so a renamed binary can't masquerade as text."""
    if doc_type == "pdf" and not data.startswith(b"%PDF"):
        raise ExtractionError("File does not look like a valid PDF.")
    if doc_type == "docx" and not data.startswith(b"PK"):
        raise ExtractionError("File does not look like a valid DOCX.")
    if doc_type in {"txt", "md", "csv", "json"}:
        if b"\x00" in data[:4096]:
            raise ExtractionError("Text file contains binary data.")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)  # HTML/Markdown comments
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    # Remove control characters except newline/tab.
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Could not decode text file.")


def _extract_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("Encrypted PDFs are not supported.")
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            txt = page.extract_text() or ""
            if txt.strip():
                pages.append(Section(title=f"Page {i}", text=txt))
    except ExtractionError:
        raise
    except Exception as exc:  # pypdf raises many types
        raise ExtractionError(f"Could not read PDF: {type(exc).__name__}") from exc
    if not pages:
        raise ExtractionError("PDF contains no extractable text (it may be a scanned image).")
    # Re-split by markdown-like headings inside the PDF text if present.
    return _split_markdown("\n\n".join(p.text for p in pages)) or pages


def _extract_docx(data: bytes) -> list[Section]:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"Could not read DOCX: {type(exc).__name__}") from exc
    sections: list[Section] = []
    title: str | None = None
    buf: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        if style.startswith("heading") or style == "title":
            if buf:
                sections.append(Section(title, "\n".join(buf)))
                buf = []
            title = text
        else:
            buf.append(text)
    for table in document.tables:
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        if rows:
            buf.append("\n".join(rows))
    if buf:
        sections.append(Section(title, "\n".join(buf)))
    return sections


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _split_markdown(text: str) -> list[Section]:
    sections: list[Section] = []
    stack: list[str] = []
    buf: list[str] = []
    current: str | None = None
    for line in text.split("\n"):
        m = _HEADING.match(line.strip())
        if m:
            if buf and "\n".join(buf).strip():
                sections.append(Section(current, "\n".join(buf)))
            buf = []
            level = len(m.group(1))
            stack = stack[: level - 1] + [m.group(2).strip()]
            # Use the two most specific heading levels as the section label.
            current = " > ".join(stack[-2:])
        else:
            buf.append(line)
    if buf and "\n".join(buf).strip():
        sections.append(Section(current, "\n".join(buf)))
    return sections


def _extract_csv(data: bytes) -> list[Section]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ExtractionError("CSV has no header row.")
    fields = [f.strip() for f in reader.fieldnames if f]
    section_col = next(
        (f for f in fields if f.lower() in {"section", "category", "topic", "question"}), None
    )
    sections: list[Section] = []
    for row in reader:
        parts = [f"{k.strip()}: {v.strip()}" for k, v in row.items() if k and v and v.strip()]
        if parts:
            title = row.get(section_col, "").strip() if section_col else None
            sections.append(Section(title or None, "\n".join(parts)))
    if not sections:
        raise ExtractionError("CSV contains no data rows.")
    return sections


def _flatten_json(value, prefix: str = "") -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            label = str(k).replace("_", " ")
            if isinstance(v, (dict, list)):
                lines += _flatten_json(v, f"{prefix}{label} - " if prefix else f"{label} - ")
            elif v not in (None, ""):
                lines.append(f"{prefix}{label}: {v}")
    elif isinstance(value, list):
        if all(not isinstance(i, (dict, list)) for i in value):
            if value:
                lines.append(f"{prefix.rstrip(' -')}: {', '.join(str(i) for i in value)}")
        else:
            for i, item in enumerate(value, 1):
                lines += _flatten_json(item, f"{prefix}{i}. ")
    elif value not in (None, ""):
        lines.append(f"{prefix.rstrip(' -')}: {value}")
    return lines


def _extract_json(data: bytes) -> list[Section]:
    try:
        obj = json.loads(_decode(data))
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Invalid JSON: {exc.msg} at line {exc.lineno}") from exc
    sections: list[Section] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).startswith("_"):
                continue  # metadata keys such as _comment
            title = str(key).replace("_", " ").capitalize()
            lines = _flatten_json(value) if isinstance(value, (dict, list)) else [f"{title}: {value}"]
            if lines:
                sections.append(Section(title, "\n".join(lines)))
    else:
        lines = _flatten_json(obj)
        if lines:
            sections.append(Section(None, "\n".join(lines)))
    if not sections:
        raise ExtractionError("JSON document contains no usable content.")
    return sections


def extract_sections(doc_type: str, data: bytes) -> list[Section]:
    if doc_type == "pdf":
        sections = _extract_pdf(data)
    elif doc_type == "docx":
        sections = _extract_docx(data)
    elif doc_type == "md":
        sections = _split_markdown(_decode(data))
    elif doc_type == "txt":
        text = _decode(data)
        sections = _split_markdown(text) or [Section(None, text)]
    elif doc_type == "csv":
        sections = _extract_csv(data)
    elif doc_type == "json":
        sections = _extract_json(data)
    else:  # pragma: no cover - guarded by detect_type
        raise ExtractionError(f"Unsupported type {doc_type}")

    cleaned = [
        Section(clean_text(s.title)[:255] if s.title else None, clean_text(s.text)) for s in sections
    ]
    cleaned = [s for s in cleaned if s.text]
    if not cleaned:
        raise ExtractionError("Document contains no extractable text.")
    return cleaned
