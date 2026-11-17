import fitz  # PyMuPDF

from .common import ExtractorFunc, Metadata, extract_line_numbered_pdf

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
#
# OPEN-560: Louisiana, Nebraska and Washington print struck words the same way (a thin drawn rule
# across the characters, no font effect; Louisiana and Nebraska publish only the PDF), and their
# rules fall inside the same window, so the same reader is reused for them unchanged. Measured
# 2026-11-14 on 104 of their real PDFs: a rule crossing a character sits at 0.41 or 0.5 of the
# span's height in Louisiana (strike) and 0.85 or more (underline); at 0.53 in Nebraska and
# Washington (strike) and 0.85 (underline); nothing else between 0.1 and 0.9 but one Louisiana
# crossing at 0.72, a signature rule after "APPROVED:" over a blank space (kept, rightly). In
# every one of the 104 the new text is the old text with characters only taken out (spacing
# aside, as for Alabama). Each state keeps its own text extractor; `read_without_struck_text`
# runs it on the copy.
#
# Only left-to-right lines are measured. The whole-archive comparison (2026-11-15, all 10,468
# Louisiana, 3,383 Nebraska and 5,818 Washington PDFs, scripts/open560_compare_struck_text.py)
# found one false strike: on Louisiana's redistricting maps (SB 116, 117, 121, 130, 136, 407 and
# their amendments, a page turned 90 degrees) the parish labels run up the page, a "line height"
# measured across them is a label's length, and the map's parish borders fell inside the band, so
# "Allen", "Calcasieu", "Caldwell" and "Orleans" were removed. Text in any other direction is now
# read as before, struck or not.
#
# OPEN-584: North Carolina prints struck words the same way, in every section of existing law that
# "reads as rewritten" (HB 613 Edition 2: "notice of renewal electronically to an e-mail address
# provided by the owner of the vehicle.renewal.", where all but the last "renewal." is struck). Its
# strike sits at 0.59 of the span's height and its underline at 0.93, so the same reader is reused
# unchanged. Whole-archive comparison (2026-11-16, all 12,399 local North Carolina PDFs,
# scripts/open560_compare_struck_text.py nc): 6,080 lose struck text, no stored text gains a
# character, no rule at 0.6 or higher removes anything, no errors. In 24 amendment papers ("A1:
# ACC-8-V-2" and the like, outside the diff lineage) pdftotext moves the "A1" label beside the
# "AMENDMENT NO." line once struck lines go; no word is added.
#
# OPEN-601: Georgia prints struck words the same way, in every change to existing law or to the
# Senate's rules (SR 1, 2026 special session: "in existence for five (5) days twenty-four (24)
# hours", where "five (5) days" is struck), and publishes only the PDF. Its strike sits at 0.5 of
# the span's height and its underline at 0.9 (and -0.5, the line above's underline), nothing in
# between, so the same reader is reused unchanged. Georgia often strikes whole printed lines;
# a line left with only its number is dropped, as before. Whole-set comparison (2026-11-16, all
# 284 PDFs of the 2026 special session, scripts/open560_compare_struck_text.py ga): 10 lose
# struck text (HR 11, HR 12, HR 14, SB 3 x5, SB 10, SR 1), no stored text gains a character,
# no rule at 0.6 or higher removes anything, no errors.
#
# OPEN-591: Nevada prints removed law inside brackets and struck through, brackets included
# (AB 4 First Reprint: "[(c)] (e) “Health care facility” means ...", where "[(c)]" is struck).
# Its bills strike at 0.55-0.60 of the span's height (one crossing at 0.45). Its amendment papers
# (A_AB4_13 and the like) mark the original bill's deletions with a red strike at 0.60-0.65 and
# the amendment's own with a purple double strike (0.55 and 0.65), all inside the band, and their
# orange double underline at 0.87, outside it. Comparison (2026-11-16, the 42 version PDFs of the
# 10-31 probe sample, 36th (2025) Special Session, scripts/open560_compare_struck_text.py nv): 33
# stored texts change, none gains a character, 2,405 "[" and 2,405 "]" before, 0 after, no
# errors. The struck sample words of the colour legend ("[omitted material]", "red
# strikethrough") go too.
_RULE_MAX_HEIGHT = 2.0
_STRIKE_LOW = 0.35
_STRIKE_HIGH = 0.7


def _struck_runs(page: "fitz.Page", rules: list) -> list:
    """The struck characters of one page, as runs of neighbouring characters in a span: each
    run is (centre x of its first character, centre x of its last, centre y)."""
    runs = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line["dir"]
            if abs(dx - 1) > 1e-3 or abs(dy) > 1e-3:
                continue  # not left-to-right text: no strike band to measure (OPEN-560)
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


def read_without_struck_text(extract: ExtractorFunc) -> ExtractorFunc:
    """`extract`, run on the PDF with its struck-out characters removed (OPEN-560)."""

    def read(data: bytes, metadata: Metadata) -> str:
        return extract(without_struck_text(data), metadata)

    read.__wrapped__ = extract  # type: ignore[attr-defined]
    return read
