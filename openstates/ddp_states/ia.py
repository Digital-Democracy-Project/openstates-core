"""
Iowa's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Version rules (OPEN-962) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-962: the scraper stores the paper behind every amendment action on a bill's page as a version named "Amendment H-<n>"
# or "Amendment S-<n>" (scrapers/ia/bills.py; filed, adopted, lost, withdrawn and out-of-order amendments alike). Read on
# the Mac replica (2026-11-28, all 6,631 Iowa documents): 1,274 such papers (821 House, 453 Senate), every one opening
# "Amend ..." and none holding "be it enacted". Three shapes: 791 line edits to the bill ("Amend House File 583 as follows:
# 1. Page 4, by striking lines 23 and 24. 2. ... 4. By renumbering as necessary."; median 777 characters), 212 amendments
# to another amendment ("Amend the amendment, H-1153, to House File 872, as follows:"; they stay fragments even when one
# strikes a page of the amendment and inserts a whole replacement, 21 of them), and 271 strikers, which replace the bill:
# "Amend House File 387 as follows: 1. By striking everything after the enacting clause and inserting: <Section 1. ...>",
# the new bill body in full (median 7,042 characters, up to 168,447). A striker keeps the enacting clause, so it carries
# no "be it enacted" for core's enacting-clause test to see; the first instruction tells it from a line edit. The line
# edits and amendments to an amendment are not the bill, so they are not versions; the strikers are whole bill texts,
# like Georgia's whole-bill SFA papers (OPEN-959), and stay versions (138 of the 271 were adopted, 133 filed and lost,
# withdrawn, ruled out of order or never acted on; whether an unadopted striker is a version is Ramon's call, see the PR).
# The note cannot tell the three shapes apart ("Amendment H-8031" for all), so the text decides, through core's
# `is_amendment_text` (OPEN-564), as for Georgia: a document with no text is never excluded.
# The head of a striker: the instruction block opens at the first colon, within the header, and its first instruction is
# the strike-everything one. "Amend the amendment ..." and "Amend the House amendment ..." never match it.
_BILL_TARGET = r"(?:house|senate)\s+(?:file|joint\s+resolution|concurrent\s+resolution|study\s+bill|resolution)\b"
AMENDMENT_TEXT_OPENING = re.compile(
    r"\A\s*amend\b"
    r"(?!\s+" + _BILL_TARGET + r"[^:]{0,250}:\s*1\.\s*by\s+striking\s+everything\s+after\s+the\s+(?:enacting|resolving)\s+clause\b)",
    re.IGNORECASE,
)


# ── Reader choice (OPEN-913; replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ────


# Iowa's bill PDFs print a margin line number on every line, and upstream reads them with `extract_line_numbered_pdf`,
# which keeps only the lines that start with a number. The governor's signed copy of an enacted bill (the scraper's
# "Signed Enrolled Bill (PDF)" document, and a few filed as "Reprinted (Marked Up)") is a different paper: the
# governor's transmittal letter ("STATE OF IOWA / KIM REYNOLDS / GOVERNOR ... Dear Mr. Secretary, I hereby transmit:
# House File 2329 ...", or the Secretary of the Senate's letter for a joint resolution) followed by a scan of the
# enrolled bill, which has no margin numbers. The scan carries an OCR text layer, so the numbered-lines reader kept
# only the odd line that began with a digit: 27 characters on average. Read on the replica and the Mac's archive copy of
# all 6,594 Iowa PDFs of the active sessions, 2026-11-26: 370 "Signed Enrolled Bill" papers (364 at 300 characters or
# fewer, 259 empty) and 57 "Reprinted (Marked Up)" papers (54 at 300 or fewer), 427 in all, every one with at most
# 2.9% of its lines starting with a number, against 23% or more for every Introduced, Prefiled, Reprinted and
# Enrolled-with-Governor's-Action paper bar one tiny bill (SF 162, 6.7%). 393 of the 427 are read in full by
# `pdftotext`; the other 34 (26 signed enrolled, 8 marked up) are image-only scans with no text layer and no fonts, which
# read empty before and after (OCR is out of scope).
# The signature is two conditions together: the text opens with a governor / Secretary of the Senate letter, and at
# most 10% of its lines start with a number (upstream's own line-numbered test in `extract_sometimes_numbered_pdf`).
# Neither alone does: 67 numbered papers have "GOVERNOR" in their first 400 characters ("BY (PROPOSED GOVERNOR
# BILL)"), and three numbered papers (SF 162's enrolled text and the amendments to SF 615 and HF 583) are under 10%. Of
# the 6,594 PDFs exactly the 393 with a text layer match; the other 6,201 read as before.
# OPEN-930: the "Enrolled" papers of two joint resolutions (SJR 9 and SJR 11, 2025) are text PDFs with no margin numbers
# and no letter: one numbered line each ("4 BE IT RESOLVED ..."), 1.6% and 2.3% of their lines, so they read as 60
# characters of 1,757 and 2,888. A paper whose title line ends "- Enrolled" and has at most 5% numbered lines is read as
# plain text too. Of the 6,631 PDFs on the archive (2026-11-27), 379 unsigned text papers carry that title; the three lowest
# numbered shares are SJR 11 (1.6%), SJR 9 (2.3%) and SF 162 (6.7%, read as before), then 16.7% and up. The title keeps
# out the other low-share papers (the amendments to SF 615 at 2.7% and HF 583 at 8.8%); the 5% line keeps out SF 162.
_LETTER = re.compile(r"GOVERNOR|Governor|Secretary\s*of\s*the\s*Senate")
_LETTER_WITHIN = 400  # characters of the paper's opening (OCR letters vary: "KliM", "KLM", "Secretary ofthe Senate"; the title line is stable)
_UNNUMBERED = 0.10
_NO_MARGIN = 0.05
_ENROLLED_TITLE = re.compile(r"[ \t]*[^\n]+ - Enrolled[ \t]*(\n|$)")  # the first line, e.g. "Senate Joint Resolution 9 - Enrolled"
_NUMBERED_LINE = re.compile(r"^\s*\d+\s+(.*)", flags=re.MULTILINE)


def is_signed_paper(text: str) -> bool:
    """True for the governor's signed copy of an enacted bill: a letter, then an unnumbered scan."""
    opening = re.sub(r"\s+", " ", text.strip())[:_LETTER_WITHIN]
    if not _LETTER.search(opening):
        return False
    lines = text.split("\n")
    return len(_NUMBERED_LINE.findall(text)) / len(lines) <= _UNNUMBERED


def is_unnumbered_enrolled_paper(text: str) -> bool:
    """True for an "Enrolled" paper that is text with no margin numbers (SJR 9, SJR 11): the title line, then at most 5% numbered lines."""
    if not _ENROLLED_TITLE.match(text):
        return False
    return len(_NUMBERED_LINE.findall(text)) / len(text.split("\n")) <= _NO_MARGIN


def readers():
    """Iowa's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext import DoNotDownload
    from openstates.fulltext.utils import pdfdata_to_text, text_after_line_numbers

    def extract_ia_pdf(data: bytes, metadata: typing.Any) -> str:
        text = pdfdata_to_text(data)
        if is_signed_paper(text) or is_unnumbered_enrolled_paper(text):
            return text
        return text_after_line_numbers(text)  # what upstream's extract_line_numbered_pdf does

    return {"application/pdf": extract_ia_pdf, "text/html": DoNotDownload}
