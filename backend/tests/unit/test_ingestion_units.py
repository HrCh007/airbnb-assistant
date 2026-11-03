"""Unit tests: text extraction, cleaning, chunking, sensitivity flags, embeddings."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import docx
import pytest

from app.rag.chunking import chunk_sections, is_sensitive_text
from app.rag.embeddings import LocalHashEmbedder
from app.rag.extractors import ExtractionError, Section, clean_text, detect_type, extract_sections, validate_magic

SAMPLES = Path(__file__).resolve().parents[3] / "data" / "sample_documents"


@pytest.mark.parametrize(
    "name,expected",
    [("a.pdf", "pdf"), ("b.DOCX", "docx"), ("c.txt", "txt"), ("d.md", "md"), ("e.markdown", "md"), ("f.csv", "csv"), ("g.json", "json")],
)
def test_detect_type(name, expected):
    assert detect_type(name) == expected


@pytest.mark.parametrize("name", ["evil.exe", "noext", "img.png", "x.html"])
def test_detect_type_rejects_unsupported(name):
    with pytest.raises(ExtractionError):
        detect_type(name)


def test_magic_validation_rejects_renamed_binaries():
    with pytest.raises(ExtractionError):
        validate_magic("pdf", b"not a pdf")
    with pytest.raises(ExtractionError):
        validate_magic("docx", b"%PDF-1.4")
    with pytest.raises(ExtractionError):
        validate_magic("txt", b"abc\x00def")
    validate_magic("pdf", b"%PDF-1.4 ...")  # ok


def test_clean_text_normalises_whitespace_and_comments():
    raw = "Hello\r\n\r\n\r\n\r\nWorld <!-- secret note -->\t\t tabs nbsp\x07"
    out = clean_text(raw)
    assert "secret note" not in out
    assert "\n\n\n" not in out
    assert "\x07" not in out
    assert "tabs nbsp" in out


@pytest.mark.parametrize("rel", sorted(str(p.relative_to(SAMPLES)) for p in SAMPLES.rglob("*.*")))
def test_all_sample_documents_extract(rel):
    path = SAMPLES / rel
    sections = extract_sections(detect_type(path.name), path.read_bytes())
    assert sections and all(s.text.strip() for s in sections)


def test_markdown_sections_follow_headings():
    md = b"# Guide\n## Wi-Fi\nPassword: abc\n## Parking\nNo parking.\n"
    secs = extract_sections("md", md)
    titles = [s.title for s in secs]
    assert "Guide > Wi-Fi" in titles and "Guide > Parking" in titles


def test_csv_rows_become_sections():
    data = b"section,answer\nLift,Yes there is a lift\nPool,No pool\n"
    secs = extract_sections("csv", data)
    assert [s.title for s in secs] == ["Lift", "Pool"]
    assert "answer: Yes there is a lift" in secs[0].text


def test_json_is_flattened_and_comment_keys_skipped():
    data = json.dumps({"_comment": "ignore me", "wifi": {"network": "N1", "password": "p@ss"}, "rules": ["no smoking", "no pets"]}).encode()
    secs = extract_sections("json", data)
    text = "\n".join(s.text for s in secs)
    assert "ignore me" not in text
    assert "password: p@ss" in text
    assert "no smoking, no pets" in text


def test_invalid_json_raises():
    with pytest.raises(ExtractionError):
        extract_sections("json", b"{not json")


def test_docx_headings_and_paragraphs():
    d = docx.Document()
    d.add_heading("House rules", level=1)
    d.add_paragraph("No smoking indoors.")
    buf = io.BytesIO()
    d.save(buf)
    secs = extract_sections("docx", buf.getvalue())
    assert secs[0].title == "House rules"
    assert "No smoking" in secs[0].text


def test_empty_document_raises():
    with pytest.raises(ExtractionError):
        extract_sections("txt", b"   \n\n  ")


# --------------------------------------------------------------------------- chunking
def test_chunk_size_and_overlap_respected():
    text = " ".join(f"Sentence number {i} talks about the apartment." for i in range(200))
    chunks = chunk_sections([Section("Long", text)], chunk_size=400, chunk_overlap=80)
    assert len(chunks) > 5
    assert all(len(c.text) <= 400 for c in chunks)
    # Overlap: consecutive chunks share some words.
    a, b = chunks[1].text.split(), chunks[2].text.split()
    assert set(a[-8:]) & set(b[:20])


def test_chunk_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_sections([Section(None, "x")], chunk_size=100, chunk_overlap=100)


def test_sensitive_sections_are_never_merged_with_normal_ones():
    secs = [
        Section("Guide > Wi-Fi", "Wi-Fi password: hunter2"),
        Section("Guide > Parking", "Street parking only."),
        Section("Guide > Lift", "There is a lift."),
    ]
    chunks = chunk_sections(secs, 900, 150)
    wifi = [c for c in chunks if "hunter2" in c.text]
    assert len(wifi) == 1 and wifi[0].is_sensitive
    assert "parking" not in wifi[0].text.lower()
    assert all(not c.is_sensitive for c in chunks if "hunter2" not in c.text)


@pytest.mark.parametrize(
    "text,sensitive",
    [
        ("Wi-Fi password: abc", True),
        ("Keybox code 1234", True),
        ("Smart lock door code: 4826#", True),
        ("Check-out time is 11 AM", False),
        ("No pets allowed", False),
    ],
)
def test_sensitivity_detection(text, sensitive):
    assert is_sensitive_text(text) is sensitive


# --------------------------------------------------------------------------- embeddings
async def test_local_embedder_is_deterministic_normalised_and_lexical():
    e = LocalHashEmbedder(1536)
    a1 = await e.embed_query("What is the Wi-Fi password?")
    a2 = await e.embed_query("what's the wifi password")
    b = await e.embed_query("Is there parking for my car?")
    assert len(a1) == 1536
    assert math.isclose(sum(v * v for v in a1), 1.0, rel_tol=1e-6)

    def cos(x, y):
        return sum(i * j for i, j in zip(x, y, strict=True))

    doc = (await e.embed_documents(["Wi-Fi network DEMO. Wi-Fi password: abc"]))[0]
    assert cos(a2, doc) > cos(b, doc)
    assert a1 == await e.embed_query("What is the Wi-Fi password?")
