"""Generate the binary sample documents (DOCX and PDF) for the Janakpuri demo
property. All content is FICTIONAL development data.

    python -m scripts.make_sample_binaries
"""

from __future__ import annotations

from pathlib import Path

import docx

OUT = Path(__file__).resolve().parents[2] / "data" / "sample_documents" / "janakpuri"

GUIDE = [
    ("title", "Demo Property – Janakpuri: Guest Guide"),
    ("p", "FICTIONAL DEVELOPMENT DATA. This is not a real property; replace it via the admin dashboard."),
    ("h", "Check-in and check-out"),
    ("p", "Check-in time is 3:00 PM. Check-out time is 12:00 PM (noon)."),
    ("p", "Early check-in from 1:00 PM can be arranged for a fee of ₹300 (fictional)."),
    ("p", "Late check-out is available until 2:00 PM if the next guest arrives later."),
    ("h", "Wi-Fi"),
    ("p", "Wi-Fi network name: DEMO-JANAKPURI. Wi-Fi password: janak-demo-9753."),
    ("h", "Access"),
    ("p", "Smart lock door code: 4826#. The code changes for every booking (fictional)."),
    ("h", "Parking"),
    ("p", "Open parking for one car is available inside the building gate on a first-come basis."),
    ("h", "Facilities"),
    ("p", "The apartment is on the second floor and the building has no lift."),
    ("p", "Air conditioning in the bedroom only. The living room has a ceiling fan and a cooler."),
    ("p", "Kitchenette with a single induction plate, kettle and refrigerator. No oven."),
    ("p", "Power backup: inverter for lights and fans."),
    ("h", "House rules"),
    ("p", "No smoking indoors. No pets. Visitors allowed until 9:00 PM. Quiet hours 10:30 PM to 7:00 AM."),
]

PDF_LINES = [
    "FICTIONAL DEVELOPMENT DATA - Demo Property Janakpuri",
    "# Emergency information",
    "Host (fictional): Demo Host Janakpuri, +91-00000-00021 (placeholder number).",
    "In an emergency call 112. Ambulance 102.",
    "Emergency exit: front staircase to the main road.",
    "# Getting around",
    "Nearest metro station noted by the host: Janakpuri West (Blue and Magenta lines), placeholder estimate 12 minutes walk.",
    "Airport: roughly 30 to 45 minutes by cab to IGI Airport (placeholder estimate).",
]


def make_docx(path: Path) -> None:
    d = docx.Document()
    for kind, text in GUIDE:
        if kind == "title":
            d.add_heading(text, level=0)
        elif kind == "h":
            d.add_heading(text, level=1)
        else:
            d.add_paragraph(text)
    d.save(path)


def make_pdf(path: Path, lines: list[str]) -> None:
    """Minimal single-page PDF with Helvetica text (no external dependencies)."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream_lines = ["BT", "/F1 11 Tf", "14 TL", "50 790 Td"]
    for ln in lines:
        stream_lines.append(f"({esc(ln)}) Tj T*")
    stream_lines.append("ET")
    content = "\n".join(stream_lines).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_docx(OUT / "janakpuri-guest-guide.docx")
    make_pdf(OUT / "janakpuri-emergency-and-transport.pdf", PDF_LINES)
    print(f"Wrote samples to {OUT}")
