import fitz  # PyMuPDF

from .common import Metadata, extract_line_numbered_pdf

# OPEN-530: Alabama prints an amendment to existing law with the removed words struck through
# and the new words underlined, both drawn as thin filled rectangles over the text, not as text
# style. pdftotext keeps every glyph, so the removed words were extracted beside the new ones
# ("property is a Class A misdemeanor D felony." in SB 292, where "A misdemeanor" is struck and
# "D felony" underlined), and LegBot described changes backwards. Alabama offers no other format
# (ALISON's GraphQL has only the *FileUrl PDF links).
#
# The two kinds of rule are told apart by height on the line. Measured 2026-11-10 over all 2,923
# Alabama 2026 PDFs: a thin rule (under 2pt tall) crossing a character sits at half the line's
# height (a strike, 824,000 crossings at 0.5 of the span's height), at its foot (an underline,
# 0.9 to 1.2) or at its top (the underline of the line above, -0.2 to 0), and never between 0.1
# and 0.4 or between 0.6 and 0.8. 984 of the 2,923 documents (514 of 1,507 bills) strike text.
# A character with a strike over more than half its width is removed from a copy of the PDF, and
# the copy is read by the same pdftotext path as before, so a PDF with no strike gives exactly
# the text it gave before. The removed words leave spaces behind (pdftotext places text by
# position); Alabama's pre-diff cleaner already ignores spacing inside a line (OPEN-529). A file
# PyMuPDF cannot open raises, so the archiver records an extraction error rather than quietly
# reading it the old way with the struck words back in.
_RULE_MAX_HEIGHT = 2.0
_STRIKE_LOW = 0.35
_STRIKE_HIGH = 0.7


def _struck_runs(page: "fitz.Page", rules: list) -> list:
    """The struck characters of one page, as runs of neighbouring characters in a span: each
    run is (centre x of its first character, centre x of its last, centre y)."""
    runs = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                x0, top, x1, bottom = span["bbox"]
                if bottom <= top:
                    continue
                strikes = [
                    rule
                    for rule in rules
                    if rule.x1 > x0
                    and rule.x0 < x1
                    and _STRIKE_LOW
                    < ((rule.y0 + rule.y1) / 2 - top) / (bottom - top)
                    < _STRIKE_HIGH
                ]
                run = None
                for char in span["chars"] if strikes else ():
                    left, right = char["bbox"][0], char["bbox"][2]
                    if any(
                        min(right, rule.x1) - max(left, rule.x0) > 0.5 * (right - left)
                        for rule in strikes
                    ):
                        x = (left + right) / 2
                        if run is None:
                            run = [x, x, (top + bottom) / 2]
                            runs.append(run)
                        run[1] = x
                    else:
                        run = None
    return runs


def without_struck_text(data: bytes) -> bytes:
    """`data` with every struck-out character removed, or `data` itself when nothing is
    struck."""
    doc = fitz.open(stream=data, filetype="pdf")
    removed = False
    for page in doc:
        rules = [
            d["rect"]
            for d in page.get_drawings()
            if d["rect"].height < _RULE_MAX_HEIGHT
        ]
        runs = _struck_runs(page, rules) if rules else []
        for first, last, y in runs:
            # From the first character's centre to the last one's, so a neighbour outside the
            # run is never caught. One mark per run: PyMuPDF slows down with many marks a page.
            page.add_redact_annot(fitz.Rect(first - 0.5, y - 0.5, last + 0.5, y + 0.5))
        if runs:
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
            removed = True
    return doc.tobytes() if removed else data


def handle_alabama_pdf(data: bytes, metadata: Metadata) -> str:
    return extract_line_numbered_pdf(without_struck_text(data), metadata)
