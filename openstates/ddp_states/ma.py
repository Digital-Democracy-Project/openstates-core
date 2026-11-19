"""
Massachusetts' action-label rules (OPEN-551, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_massachusetts`, BROKER-481), unchanged: the broker drops it in BROKER-490 once this rule is live.

A budget's line-item vetoes are not a veto of the bill. The Governor signs it in part and returns the vetoed items:
H 4240 (194th, the FY2026 budget) carries "Signed (in part) by the Governor, Chapter 9 of the Acts of 2025" with no
label, then "Governor returned to the House with vetoes and amendments to certain sections" tagged `executive-veto`.
So the "vetoes and amendments to certain sections" line loses `executive-veto`, and the "Signed (in part) by the
Governor" line gets `executive-signature`, as every other "Signed by the Governor, Chapter N" line already has.
Those are the only two such lines stored (Mac replica, 2026-11-17); the rule is for the next budget.

No `classify_bill` here: Massachusetts' bill labels (reports, communications and other papers that are not bills) are
set by `os-text-extract relabel-non-bills` (`non_bill_kind`, OPEN-517), a separate path this module leaves alone.
"""

import re
import typing
from openstates.ddp_states._text import _PAGE_FOOTER_TAIL_RE, _drop_page_footer_lines

from openstates.ddp_states import NON_BILL_AMENDMENT

_SECTION_VETOES = re.compile(r"\bvetoes and amendments to certain sections\b", re.I)
_SIGNED_IN_PART = re.compile(r"^Signed \(in part\) by the Governor\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `executive-veto` on a line-item veto message, `executive-signature` on a signature in
    part, every other label as given."""
    text = description or ""
    labels = list(classification)
    # Both checks run on every line, so a line matching both gets both corrections in one call (stable on its output).
    if _SECTION_VETOES.search(text):
        labels = [c for c in labels if c != "executive-veto"]
    if _SIGNED_IN_PART.match(text) and "executive-signature" not in labels:
        labels.append("executive-signature")
    return labels


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-400: Massachusetts-only text cleaning applied to prior_text/raw_text immediately before
# difflib.unified_diff() (never touching the stored raw_text, never applied to another
# jurisdiction). Massachusetts is the heaviest case of the page-footer tail described at
# _PAGE_FOOTER_TAIL_RE: 9,233 of 10,711 Massachusetts PDF documents contain one (48,688 lines;
# the first footer is "of 1" in 86% of them) and none of the 297 HTML documents does. It is also
# in 289 of the 291 stored Massachusetts diffs, 1,814 lines.
#
# OPEN-223: OPEN-400 stopped there, reasoning that both diff paths compare like-for-like media
# types since OPEN-217. They do not for Massachusetts: every Massachusetts comparison is the
# bill as filed (PDF "Bill Text") against the law as signed (HTML "Chapter Law Text (Enacted)"),
# two versions with two renderings (see _diff_baseline). Measured 2026-11-13 on the production
# replica, all 364 Massachusetts bills with two documents (291 with a stored comparison): 193 of
# the 291 had no unchanged line at all, because the renderings differ, not the words.
#   1. The chapter law alone opens with the act's title ("AN ACT ...") and the enacting clause
#      ("Be it enacted by the Senate and House of Representatives ...", 362 of 364 as its own
#      line, one joined to "SECTION 1.") and closes with "Approved, <date>." ("Approved (in part),"
#      for a budget with vetoes). The bill as filed has none of the three (one PDF has the title).
#   2. The PDF wraps at about 90 characters; the HTML has one long line per paragraph with runs
#      of spaces.
#   3. The PDF puts the text being inserted into a law between double quotes; the HTML does not.
# What it does: drop the footer tail; for the bill-against-law comparison only (`cross_media`, a
# PDF against an HTML), also drop the title line (one of the first three lines, all capitals), the
# enacting clause and the "Approved" line, and remove double quotes; join everything with single
# spaces; then put one clause per line, breaking after ". ; : ," and after ":-", the way
# Massachusetts introduces inserted text. Two documents of the same rendering keep their title,
# "Approved" line and quotes, so a change to any of them still shows (pm-review). Sentences alone (the Nebraska shape, OPEN-486) still
# left 33 of the 291 with no unchanged line, H 4252 among them: its four edits fall in every one
# of its sentences. The title, enacting clause and "Approved" line are real legal text, dropped
# because each exists on one side only, so it can only ever be an addition.
# Result over the 291 stored comparisons (words on changed lines, so line length does not count):
#   no unchanged line   193 -> 7 (each a short bill reworded throughout; 8 more become empty)
#   changed words       1,221,228 -> 591,483    worse (more changed words) 0
#   title / enacting clause / "Approved" as additions   869 lines in 291 -> 0
#   H 5555, H 4240: 1.50 MB comparisons of 1.33 MB documents -> 1.23 MB and 1.21 MB (budgets,
#   whose line items really were rewritten)
# The 73 bills whose chapter law was archived after OPEN-217 get a comparison too (4 with no
# unchanged line).
_MA_TITLE_LINE_RE = re.compile(r"AN ACT\b[^a-z]*")


_MA_ENACTING_CLAUSE = (
    "Be it enacted by the Senate and House of Representatives in General Court assembled, "
    "and by the authority of the same, as follows:"
)


_MA_APPROVED_LINE_RE = re.compile(
    r"Approved(?: \(in part\))?, [A-Z][a-z]+ \d{1,2}, \d{4}\."
)


_MA_DOUBLE_QUOTE_RE = re.compile('["\u201c\u201d]')


_MA_CLAUSE_BREAK = re.compile(r"(?:(?<=[.;:,])|(?<=:-))\s+")


def _clean_massachusetts_text(text: str, *, cross_media: bool) -> str:
    """Massachusetts-only cleaner, see the block comment above: page-footer tails dropped (and,
    across renderings, the title, the enacting clause, the "Approved" line and double quotes),
    then one clause per line."""
    kept = []
    seen = 0
    for line in _drop_page_footer_lines(text, _PAGE_FOOTER_TAIL_RE).split("\n"):
        line = " ".join(line.split())
        if not line:
            continue
        seen += 1
        if cross_media:
            if seen <= 3 and _MA_TITLE_LINE_RE.fullmatch(line):
                continue
            if line.startswith(_MA_ENACTING_CLAUSE):
                line = line[len(_MA_ENACTING_CLAUSE):].lstrip()
            if not line or _MA_APPROVED_LINE_RE.fullmatch(line):
                continue
        kept.append(line)
    words = " ".join(kept)
    if cross_media:
        words = _MA_DOUBLE_QUOTE_RE.sub("", words)
    words = " ".join(words.split())
    return "\n".join(part for part in _MA_CLAUSE_BREAK.split(words) if part)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Massachusetts's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    cross_media = prior_media_type != cur_media_type
    return (
        _clean_massachusetts_text(prior_text, cross_media=cross_media),
        _clean_massachusetts_text(raw_text, cross_media=cross_media),
    )


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


REAL_MEASURE_TITLE_PATTERN = re.compile(
    r"\A\s*(an act|a resolve|resolves?|resolutions?|joint resolution|proposal)\b",
    re.IGNORECASE,
)

NON_BILL_KIND_RULES = (
    (NON_BILL_AMENDMENT, re.compile(r"\A\s*amendment\b", re.IGNORECASE)),
    (
        "communication",
        re.compile(
            r"\A\s*(?:(?:a|the)\s+)?communications?\s+from\b"
            r"|\A\s*governor'?s\s+message\b"
            r"|\A\s*so\s+much\s+of\s+the\s+message\b"
            r"|\bmessage\s+from\b"
            r"|\bstate\s+of\s+the\s+commonwealth\s+address\b",
            re.IGNORECASE,
        ),
    ),
    (
        "study request",
        re.compile(r"\A\s*study\s+order\b|\binvestigation\s+and\s+study\b", re.IGNORECASE),
    ),
    ("order", re.compile(r"\A\s*(?:an\s+)?order\b", re.IGNORECASE)),
)

NON_BILL_STUDY_ORDER_ACTION = re.compile(r"\A\s*order\s+reported\s+favorably\s+by\s+committee\b", re.IGNORECASE)

# an order not titled as one up front: "... - Extension Order", "Extension Order H.4819",
# "Proposed House Rules Amendment Order", a precept, a canvass of an election's votes
NON_BILL_OTHER_ORDER = re.compile(
    r"\border\b|\A\s*precept\b|\A\s*canvassing\s+the\s+votes\b", re.IGNORECASE
)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Massachusetts's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_line_numbered_pdf, extractor_for_elements_by_xpath
    return {
        "application/pdf": extract_line_numbered_pdf,
        # Added 2026-08-14 (OPEN-37): the enacted Chapter-of-the-Acts page
        # (scrapers/ma/bills.py's "Chapter Law Text (Enacted)" version) is
        # plain HTML with no PDF variant. The chapter title and body text
        # live in two sibling <div class="col-xs-12"> elements -- the first
        # holding <h2 class="h3 chapterTitle">, the second the actual
        # <p>-tag body ending in "Approved, <date>." -- verified directly
        # against 5 real chapter-law pages (Chapter139/2024, Chapter15/3/18
        # of 2025 -- the exact bills this ticket names, H972/H4100/H4004 --
        # and Chapter163/2026).
        "text/html": extractor_for_elements_by_xpath(
            "//div[h2[contains(@class, 'chapterTitle')]] | "
            "//div[h2[contains(@class, 'chapterTitle')]]/following-sibling::div[1]"
        ),
    }
