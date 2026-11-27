"""
the United States (Congress)'s per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing


# ── Reader choice (OPEN-554 moved it here; OPEN-917 gave the PDF its own reader) ─────────────────────────────────────


# OPEN-917: upstream reads Congress's PDFs with `extract_sometimes_numbered_pdf`: a paper with margin line numbers on
# more than 10% of its lines is read with `extract_line_numbered_pdf`, which keeps only the lines that start with a
# number. A federal bill prints the numbers beside its text lines but not beside a section heading or a table-of-contents
# entry ("Sec. 1. Short title; table of contents.", "TITLE I", "Sec. 101. Purpose."), and a simple resolution numbers only
# its "Resolved" clause, so its "Whereas" paragraphs were dropped (S.Res. 54 kept 738 characters of 4,197). Read on the
# Mac's archive copy (2026-11-27): 3,682 of 7,202 resolutions change (all gain), and a bill gains its heading lines
# (S. 241: 24 lines, HR 8540: 38; the OPEN-911 sweep's larger counts also counted the printer's page furniture).
# The body now starts at the first line after the paper's title block (the line after "A BILL" / "RESOLUTION" /
# "JOINT RESOLUTION" / "AN ACT" and the title under it, up to a "Whereas"; the first numbered line when there is none, or when it comes
# first) and runs to the last numbered line; an unnumbered line in between is body text and is kept, as Oklahoma's reader
# does (OPEN-914). What is not body, as before, stays out: a line that is only a number (a page number), and the
# government printer's page furniture: the "VerDate ..." line, the typesetter's "<user> on <terminal> with BILLS" mark
# and the "•HR 8540 IH" / "HR 33 RFS" identifier at a page's foot. Everything before the title block (the cover) and
# after the last numbered line (an enrolled bill's signature block) stays out, as before.
_NUMBERED = re.compile(r"\s*\d+\s+(.*)")
_BARE_NUMBER = re.compile(r"\s*\d+\s*\Z")
_TITLE_HEADING = re.compile(r"\s*(?:A BILL|AN ACT|(?:(?:JOINT|CONCURRENT) )?RESOLUTION)\s*\Z")
_PAGE_FURNITURE = re.compile(
    r"\s*(?:(?:"
    r"VerDate\b.*"  # the printer's footer: date, "Jkt", file name
    r"|\S+ on [A-Z0-9]+ with (?:BILLS|\$\$_JOB)"  # typesetter's mark: "kjohnson on DSK79L0C42PROD with BILLS"
    # page foot: "•HR 9667 IH", "HR 33 RFS", "† S 2132 ES" (a page number can run into it: "SRES 312 IS1S")
    r"|\W?\s*[HS][A-Z]{0,6} \d+ [A-Z]{2,4}[A-Z0-9]{0,2}"
    r")\s*)+\Z"  # the mark and the foot share a line on some pages
)


def _body_start(lines: typing.List[str], first_numbered: int) -> int:
    """The index where the body begins: after the title block that follows the paper's last "A BILL" / "RESOLUTION"
    heading before the first numbered line, or that numbered line when there is no such heading."""
    headings = [i for i in range(first_numbered) if _TITLE_HEADING.match(lines[i].replace("\f", ""))]
    if not headings:
        return first_numbered
    i = headings[-1] + 1
    # the title under the heading (a concurrent resolution has none: its first "Whereas" follows the heading directly)
    while i < first_numbered and lines[i].replace("\f", "").strip() and not lines[i].replace("\f", "").lstrip().startswith("Whereas"):
        i += 1
    while i < first_numbered and not lines[i].replace("\f", "").strip():
        i += 1
    return min(i, first_numbered)


def extract_us_pdf(data: bytes, metadata: typing.Any) -> str:
    # imported at call time: openstates.fulltext builds its reader table from this module
    from openstates.fulltext.utils import pdfdata_to_text

    text = pdfdata_to_text(data)
    lines = text.split("\n")
    # upstream's switch between the numbered and the plain reading, unchanged (`extract_sometimes_numbered_pdf`)
    # (counted over the whole text, as upstream does: its `\s+` can run across a line break)
    numbered = re.findall(r"^\s*\d+\s+(.*)", text, flags=re.MULTILINE)
    if len(numbered) / len(lines) <= 0.10:
        return text
    at = [i for i, line in enumerate(lines) if _NUMBERED.match(line)]
    if not at:
        return ""  # as `extract_line_numbered_pdf` returns
    first, last = _body_start(lines, at[0]), at[-1]
    kept = []
    for i, line in enumerate(lines):
        match = _NUMBERED.match(line)
        if match:
            kept.append(match.group(1))
        elif first <= i < last:
            bare = line.replace("\f", "")
            if bare.strip() and not _BARE_NUMBER.match(bare) and not _PAGE_FURNITURE.match(bare):
                kept.append(bare.strip())
    return "\n".join(kept)


def readers():
    """the United States (Congress)'s text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    from openstates.fulltext.us import handle_us_bill_xml
    return {
        # Found 2026-08-12: DoNotDownload here (inherited unchanged from public upstream,
        # predates this fork entirely) skipped every US bill's XML unconditionally. Unlike
        # AZ's PDF bug, this cost nothing in practice -- verified directly that all 44,401 US
        # versions with an XML link also have a PDF link, and PDF extraction already succeeds
        # 100% of the time -- but XML (govinfo's bill.dtd format for most stages, USLM for
        # Enrolled Bills) is cleanly structured with no page-break/line-wrap artifacts, making
        # it a meaningfully better diffing source than PDF's line-numbered extraction. See
        # `handle_us_bill_xml` and archive_bill_versions()'s prior_text preference order below.
        "text/xml": handle_us_bill_xml,
        "application/pdf": extract_us_pdf,
    }
