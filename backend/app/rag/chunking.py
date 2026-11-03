"""Structure-aware chunking.

Sections (headings, CSV rows, JSON keys) are kept intact when they fit. Longer
sections are split recursively on paragraph -> line -> sentence -> word
boundaries with configurable size and overlap (in characters). Small adjacent
sections sharing a parent heading are merged so chunks are not too tiny.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.extractors import Section

_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "]

# Content mentioning these is flagged sensitive (only served for the guest's own
# property and, when the property has a guest PIN, only after verification).
_SENSITIVE = re.compile(
    r"(?i)(wi-?fi\s*(password|pass|key|code)|password|passcode|door\s*code|lock\s*code|"
    r"key\s*box|lockbox|key\s*safe|access\s*code|smart\s*lock|pin\s*code|gate\s*code)"
)


@dataclass
class Chunk:
    index: int
    section: str | None
    text: str
    is_sensitive: bool


def is_sensitive_text(text: str) -> bool:
    return bool(_SENSITIVE.search(text))


def _split_recursive(text: str, size: int, seps: list[str]) -> list[str]:
    if len(text) <= size:
        return [text]
    for i, sep in enumerate(seps):
        if sep in text:
            parts = text.split(sep)
            out: list[str] = []
            for p in parts:
                piece = p + (sep if sep.strip() in ".?!;," else "")
                if len(piece) > size:
                    out.extend(_split_recursive(piece, size, seps[i + 1 :]))
                else:
                    out.append(piece)
            return [o for o in out if o.strip()]
    # No separators left: hard split.
    return [text[i : i + size] for i in range(0, len(text), size)]


def _pack(pieces: list[str], size: int, overlap: int, joiner: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{joiner}{piece}" if current else piece
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap > 0 else ""
            # Start overlap at a word boundary.
            if tail and " " in tail:
                tail = tail[tail.index(" ") + 1 :]
            current = f"{tail}{joiner}{piece}" if tail else piece
            if len(current) > size:
                current = piece
        else:
            current = piece
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_sections(
    sections: list[Section], chunk_size: int = 900, chunk_overlap: int = 150
) -> list[Chunk]:
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    # 1. Merge small neighbouring sections with the same top-level heading.
    merged: list[Section] = []
    for s in sections:
        if merged:
            prev = merged[-1]
            same_parent = (prev.title or "").split(" > ")[0] == (s.title or "").split(" > ")[0]
            tiny = len(prev.text) < 160 and len(s.text) < 160
            # Never mix sensitive facts (passwords, codes) with ordinary content.
            mixed = is_sensitive_text(prev.text) != is_sensitive_text(s.text) or is_sensitive_text(s.text)
            if same_parent and tiny and not mixed and len(prev.text) + len(s.text) + 40 < chunk_size // 2:
                label = f"{s.title}\n" if s.title and s.title != prev.title else ""
                merged[-1] = Section(prev.title, f"{prev.text}\n\n{label}{s.text}")
                continue
        merged.append(s)

    # 2. Split long sections; prefix each chunk with its heading for context.
    chunks: list[Chunk] = []
    for s in merged:
        header = f"{s.title}\n" if s.title else ""
        budget = max(chunk_size - len(header), 200)
        pieces = _split_recursive(s.text, budget, _SEPARATORS)
        for text in _pack(pieces, budget, chunk_overlap, " " if len(pieces) > 1 else ""):
            body = f"{header}{text}".strip()
            chunks.append(
                Chunk(
                    index=len(chunks),
                    section=s.title,
                    text=body,
                    is_sensitive=is_sensitive_text(body),
                )
            )
    return chunks
