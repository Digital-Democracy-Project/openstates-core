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


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Louisiana's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    # OPEN-560: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
