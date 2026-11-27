"""
Iowa's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


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
# Known limit: two joint resolutions' "Enrolled" papers (SJR 9, SJR 11), text PDFs with no margin numbers and no letter,
# still read as 60 characters; they are not a signed paper and are left for a ticket of their own.
_LETTER = re.compile(r"GOVERNOR|Governor|Secretary\s*of\s*the\s*Senate")
_LETTER_WITHIN = 400  # characters of the paper's opening (OCR letters vary: "KliM", "KLM", "Secretary ofthe Senate"; the title line is stable)
_UNNUMBERED = 0.10
_NUMBERED_LINE = re.compile(r"^\s*\d+\s+(.*)", flags=re.MULTILINE)


def is_signed_paper(text: str) -> bool:
    """True for the governor's signed copy of an enacted bill: a letter, then an unnumbered scan."""
    opening = re.sub(r"\s+", " ", text.strip())[:_LETTER_WITHIN]
    if not _LETTER.search(opening):
        return False
    lines = text.split("\n")
    return len(_NUMBERED_LINE.findall(text)) / len(lines) <= _UNNUMBERED


def readers():
    """Iowa's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext import DoNotDownload
    from openstates.fulltext.utils import pdfdata_to_text, text_after_line_numbers

    def extract_ia_pdf(data: bytes, metadata: typing.Any) -> str:
        text = pdfdata_to_text(data)
        if is_signed_paper(text):
            return text
        return text_after_line_numbers(text)  # what upstream's extract_line_numbered_pdf does

    return {"application/pdf": extract_ia_pdf, "text/html": DoNotDownload}
