"""
Louisiana's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing

from openstates.ddp_states._text import _drop_page_footer_lines


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-496: Louisiana-only text cleaning, the same page-footer drop as Massachusetts above but for
# the whole footer. Louisiana's PDFs keep the full centred footer line ("Page 1 of 2", 38 or more
# leading spaces), so there is no bare "of N" tail to match. Measured 2026-11-06 on the production
# replica (a random 400 Louisiana bills with two or more versions, 937 stored comparisons): 190
# comparisons held a footer line (468 lines), none after cleaning, none got worse. When text is inserted every later page number shifts, so these lines show
# up as changes and cascade. Only a line that is nothing but "Page N of M" is dropped: real text that
# mentions a page ("On page 10, line 16, change ...", "after page 2 of this") never matches because
# the whole line has to be the footer. Nothing else is stripped (change bars and case-only changes
# were not found in Louisiana).
_LA_PAGE_FOOTER_RE = re.compile(r"^\s*Page\s+\d{1,4}\s+of\s+\d{1,4}\s*$", re.IGNORECASE)


def _clean_louisiana_text(text: str) -> str:
    """Louisiana-only cleaner, see the block comment above: drops "Page N of M" footer lines and
    leaves everything else untouched."""
    return _drop_page_footer_lines(text, _LA_PAGE_FOOTER_RE)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Louisiana's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_louisiana_text(prior_text), _clean_louisiana_text(raw_text)


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


# OPEN-491: Louisiana's amendment papers -- "<House|Senate> Committee Amendment, #<N>, <name>,
# <Draft|Adopted|Proposed>", "<House|Senate> Floor Amendment, #<N>, <name>, <Adopted|Proposed|
# Rejected|Withdrawn>", "<House|Senate> [Floor ]Legislative Bureau Amendment, #<N>, <name>,
# <Adopted|Proposed>" and "Senate Floor Bureau Note, #<N>, <name>, Adopted" -- are not versions
# of the bill (Ramon, 2026-11-04: "Leave them out"), the same call as Nebraska's. The real
# versions stay: Original, Engrossed, Reengrossed, Enrolled, Act. The name can itself hold a
# comma ("Owen, Robert"), so it is matched loosely between the number and the closing word.
# Read on the production replica (2026-11-04): all 3,328 of Louisiana's non-version documents
# match (committee 2,273, floor 811, bureau amendments 147, bureau notes 97), none of the
# 7,140 other documents (55 of them Conference Committee Reports, OPEN-511 below) does, and
# no other state has a note of any of these shapes. 79% of their stored comparisons replaced the whole
# document (OPEN-480 Stage 4 trial). Their stage is untouched (committee, floor and bureau
# amendments already rank as amendments; the bureau note stays unknown, it is not bill text).
# Prospective only: a Louisiana recompute-diff-order applies it to stored diffs, a separate,
# authorised step.
# OPEN-511: Louisiana's Conference Committee Reports -- "Conference Committee Report, #<N>,
# House <status>, Senate <status>" -- are left out too. The report is the conferees' letter
# listing the amendments they recommend ("recommend the following concerning the Reengrossed
# bill: ... AMENDMENT NO. 1 On page 1, line 2, ..."), not a re-typeset bill, so comparing a
# version against it double-counts changes (OPEN-480 dev LegBot sample, 2026-11-08). Read on the
# Mac replica (2026-11-08): 55 documents, every one of this shape (statuses Adopted, Proposed,
# Rejected, Withdrawn), and no other state has a note of this shape. The next real version
# (Enrolled, Act) now diffs against the last real version instead. Its stage is untouched.
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:"
    r"(?:house|senate) committee amendment, #[1-9]\d*, .+, (?:draft|adopted|proposed)"
    r"|(?:house|senate) floor amendment, #[1-9]\d*, .+, (?:adopted|proposed|rejected|withdrawn)"
    r"|(?:house|senate) (?:floor )?legislative bureau amendment, #[1-9]\d*, .+, (?:adopted|proposed)"
    r"|senate floor bureau note, #[1-9]\d*, .+, adopted"
    r"|conference committee report, #[1-9]\d*,"
    r" house (?:adopted|proposed|rejected|withdrawn), senate (?:adopted|proposed|rejected|withdrawn)"
    r")\Z",
    re.IGNORECASE,
)


# ── Reader choice (OPEN-915 adds the page rule below; the struck-text wrapper is OPEN-560's) ───────────────────────


# OPEN-915: upstream's `extract_sometimes_numbered_pdf` reads a Louisiana PDF with margin line numbers by keeping only the
# lines that start with a number. That keeps all of a bill's law text (every line of it is numbered) and drops what is
# not: the cover block (title, authors, summary line), the signature block, the page headers and footers, the DIGEST, and
# any page with no margin numbers at all. Read on the Mac's archive copy of all 10,468 Louisiana 2026 PDFs: the law text of
# the redistricting bills SB 136, HB 699 and SB 121 is complete (SB 121 Enrolled, pages 1 to 7). What they lose is the
# "Plan Statistics" appendix the legislature attaches after the instrument (population and voter registration tables by
# district and parish, 14 pages in SB 136), and behind it map pages whose labels are not text worth keeping.
# Louisiana numbers the pages of the instrument "Page N of N", digest included ("Page 14 of 14" in SB 136), and appends
# attachments after the last one. The digest ("DIGEST", then "The digest printed below ... constitutes no part of the
# legislative instrument") is the drafters' summary, not law, and the scraper stores it as its own document link
# (OPEN-902); it stays as it was, which is out of the text apart from its few lines that start with a number.
# The rule: in a paper read with margin numbers, a page after the instrument's last page that has no margin number at
# all is kept in full. Map pages carry numbers (their precinct labels) and are read as before, as is everything else, so
# no line the old reader kept is lost (measured, see the PR). A page of the instrument with no numbers is not kept
# either (the digest's last pages, HB 1's table of contents). A margin number alone on its line counts as a number: a
# map page whose only number-like line is a bare precinct label ("4") is not an attachment table.
# Each footer must follow the one before it ("Page 6 of 14", then "Page 7 of 14"); one that does not is ignored, so a
# paper whose end cannot be told keeps the old reading throughout. Read on all 9,595 archived papers read with margin
# numbers (2026-11-26): every one has its footers 1..M in order, exactly one with N = M, and it is the last footer.
_LA_MARGIN_NUMBER = re.compile(r"\s*\d+(?:\s|$)")  # a margin number, alone on its line or before text
_LA_LAST_PAGE = re.compile(r"^\s*Page\s+(\d+)\s+of\s+(\d+)\s*$", re.IGNORECASE)
# upstream's choice between the two readings, copied as it counts (over all lines, blank ones too)
_LA_UPSTREAM_NUMBERED = re.compile(r"^\s*\d+\s+(.*)", flags=re.MULTILINE)
_LA_NUMBERED_SHARE = 0.10


def extract_la_pdf(data: bytes, metadata: typing.Any) -> str:
    # imported at call time: openstates.fulltext builds its reader table from this module
    from openstates.fulltext.utils import pdfdata_to_text, text_after_line_numbers

    text = pdfdata_to_text(data)
    if len(_LA_UPSTREAM_NUMBERED.findall(text)) / len(text.split("\n")) <= _LA_NUMBERED_SHARE:
        return text  # no margin numbers: read as it is printed, as upstream does
    kept = []
    after_instrument = False
    page_no = total = 0  # the last footer accepted: its N and its M
    for page in text.split("\f"):
        lines = page.split("\n")
        if after_instrument and not any(_LA_MARGIN_NUMBER.match(line) for line in lines):
            kept.extend(line.strip() for line in lines if line.strip())  # an attachment page
        else:
            numbered = text_after_line_numbers(page)
            if numbered:
                kept.append(numbered)
        for line in lines:
            footer = _LA_LAST_PAGE.match(line)
            if footer and (total == 0 or (int(footer.group(1)), int(footer.group(2))) == (page_no + 1, total)):
                page_no, total = int(footer.group(1)), int(footer.group(2))
                if page_no == total:
                    after_instrument = True
    return "\n".join(kept)


def readers():
    """Louisiana's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    # OPEN-560: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_la_pdf)}
