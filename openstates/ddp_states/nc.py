"""
North Carolina's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-371: North Carolina-only text cleaning applied to prior_text/raw_text immediately before
# difflib.unified_diff() (never touching the stored raw_text, and never applied to any other
# jurisdiction). Measured 2026-10-15 on the production replica (OPEN-352 / OPEN-359):
#
# 1. The "Ratified" printing and the "SL <year>-<N>" session law are typeset differently from the
#    "Edition N" printings: every line is indented, there is a header block ("GENERAL ASSEMBLY OF
#    NORTH CAROLINA / SESSION 2025 / HOUSE BILL 26 / RATIFIED BILL", plus a tracking line such as
#    "*H26-v-4*"), a running page header or footer on every page ("Page 2 House Bill 26-Ratified",
#    "House Bill 26-Ratified Page 3", or the Session Law equivalents), and the Editions' own "A BILL
#    TO BE ENTITLED" line is missing. A raw Edition -> Ratified diff was therefore 5 to 10 times
#    larger than the real change (HB26 34,414 characters raw, 3,899 cleaned; SB50 58,140 / 5,276).
# 2. Every Ratified (164 of 164) and Session Law (158 of 158) document ends with the same block
#    after the bill text: "In the General Assembly read three times and ratified this the ...",
#    then 9 to 13 lines of signatures and the Governor's approval, none of it bill text. It is cut
#    at that marker.
#
# A scan of every North Carolina Filed, Edition, Ratified and Session Law document on the
# production replica found none of these header, tracking or page-line shapes in any of the 5,566
# Filed and Edition documents ("A BILL TO BE ENTITLED" is the one exception, once, near the top),
# so removing a whole line that matches one cannot remove Edition text. A bare "Page N" line was
# also considered and matched no document at all, so it is not included.
#
# What is NOT done, on purpose: no re-wrapping of lines. The measured remainder after the steps
# below was real amendment text (HB171 Edition 3 -> Ratified), not wrapping.
_NC_RATIFICATION_MARKER_RE = re.compile(
    r"^In the General Assembly read three times and ratified", re.IGNORECASE
)


_NC_NOISE_LINE_RES = tuple(
    re.compile(pattern)
    for pattern in (
        r"A BILL TO BE ENTITLED",
        r"GENERAL ASSEMBLY OF NORTH CAROLINA",
        r"SESSION \d{4}",
        r"(?:HOUSE|SENATE) BILL \d+",
        r"RATIFIED BILL",
        r"SESSION LAW \d{4}-\d+",
        r"\*[HS]\d+-v-\d+\*",
        r"Page \d+ (?:House|Senate) Bill \d+-Ratified",
        r"(?:House|Senate) Bill \d+-Ratified Page \d+",
        r"Page \d+ Session Law \d{4}-\d+ (?:House|Senate) Bill \d+",
        r"(?:House|Senate) Bill \d+ Session Law \d{4}-\d+ Page \d+",
    )
)


_NC_WHITESPACE_RUN = re.compile(r"\s+")


def _clean_nc_text(text: str) -> str:
    """North Carolina-only cleaner, see the block comment above: whitespace and blank lines
    normalised, the header block and running page lines dropped, and the signature block after the
    ratification marker cut."""
    kept = []
    for line in text.split("\n"):
        line = _NC_WHITESPACE_RUN.sub(" ", line).strip()
        if not line:
            continue
        if _NC_RATIFICATION_MARKER_RE.match(line):
            break
        if any(pattern.fullmatch(line) for pattern in _NC_NOISE_LINE_RES):
            continue
        kept.append(line)
    return "\n".join(kept)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """North Carolina's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_nc_text(prior_text), _clean_nc_text(raw_text)


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


# OPEN-594: every "A<n>: ..." paper, the stamp page above and the amendment text ("A1: ABR-9-V-1",
# "A10: AMT-24-V-5", "A1: s391-asu-15-v-3"). OPEN-366 left the amendment text in because no stage rule
# placed it; since OPEN-540 a version that cannot be placed counts as full text (Ramon, 2026-11-16), so
# api-v3 served these 206 papers as bill texts and the website showed each as an empty "What changed"
# step. Only a numbered paper takes any suffix; the un-numbered one stays the stamp page "A: Amendment"
# alone. Read on the production replica (2026-11-17, every North Carolina session: 2025 and 2025E1):
# 53 stamp-page notes (54 stored documents) + 206 amendment texts have this shape; none of the 5,947
# real versions does (Edition N 3,233, Filed 2,343,
# Ratified 165, SL N-N 159, Adopted 29, Resolution N-N 18). Their diffs do not change: with no stage
# they were already outside the comparison chain. Stored rows: `recompute-diff-order nc --flags-only`.
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(r"\A(?:A[1-9]\d*: .+|A: Amendment)\Z", re.IGNORECASE)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """North Carolina's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    # OPEN-584: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
