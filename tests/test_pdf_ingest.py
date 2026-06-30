"""PDF ingest path (ADR 0008): text-layer extraction and text sectioning.

The OCR fallback is not tested here; it needs system binaries (tesseract,
poppler) that CI does not carry. These tests cover the pure-Python text path and
the heading inference, which is where the logic lives.
"""

from __future__ import annotations

from assistant.ingest import extract_pdf_text, sections_from_text


def _make_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF with a text layer, built by hand so the test
    needs only pypdf (to read), not a PDF generator (to write)."""
    def esc(s: str) -> bytes:
        return s.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)").encode("latin-1")

    # One text-showing operator per line, moved down the page between lines.
    shown = b"BT /F1 12 Tf 72 720 Td 14 TL "
    for i, line in enumerate(lines):
        if i:
            shown += b"T* "
        shown += b"(" + esc(line) + b") Tj "
    shown += b"ET"

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(shown)).encode() + b" >>\nstream\n" + shown + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return out


def test_extract_pdf_text_reads_the_text_layer():
    pdf = _make_pdf(["Reduced Fare", "Seniors 65 and older pay $1.00 per ride."])
    text = extract_pdf_text(pdf)
    assert "Reduced Fare" in text
    assert "$1.00" in text


def test_sections_from_text_infers_headings():
    # Bodies are long enough (>200 chars) that the tiny-fragment merge leaves
    # each as its own section, the way real policy sections behave.
    text = (
        "Reduced Fare Program\n"
        "Seniors 65 and older qualify for the reduced fare on every fixed-route "
        "service the district operates. Proof of age is required when boarding, "
        "and an agency courtesy card or a Medicare card is also accepted as proof "
        "of eligibility for the reduced fare.\n"
        "Veteran Discount\n"
        "Veterans pay the reduced fare with a valid veteran identification card. "
        "The discount applies to all fixed-route services in the district and is "
        "available at every fare vending machine and through the mobile ticketing "
        "application used across the region.\n"
    )
    by_heading = dict(sections_from_text(text))
    assert "Reduced Fare Program" in by_heading
    assert "Veteran Discount" in by_heading
    assert "Proof of age is required" in by_heading["Reduced Fare Program"]


def test_sentences_are_not_treated_as_headings():
    # A line ending in a period is body text, never a section heading.
    text = (
        "This is a full sentence that should be body text, not a heading.\n"
        "Another sentence follows it here with enough length to be kept.\n"
    )
    sections = sections_from_text(text)
    assert [h for h, _ in sections] == ["(document start)"]


def test_pdf_round_trip_through_sectioning():
    pdf = _make_pdf([
        "Discount Eligibility",
        "Riders 65 and older qualify for the senior discount fare on all routes.",
        "Proof of age such as a driver license is required when you board the bus.",
    ])
    sections = dict(sections_from_text(extract_pdf_text(pdf)))
    assert "Discount Eligibility" in sections
    assert "senior discount" in sections["Discount Eligibility"]
