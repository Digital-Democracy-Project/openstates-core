"""
Hawaii's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Action labels (OPEN-824) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-824: in Hawaii a bill passes a chamber on Third Reading (or Final Reading, after conference). Before that, each
# committee that reports the bill out has its report adopted on the floor, and the line says what happens next:
# "Report adopted; Passed Second Reading ... and referred to WAM." (Senate), "Report adopted; referred to the
# committee(s) on FIN ..." (House), "Report adopted, as amended (SD 1) and recommitted to EDU." The scraper's
# categorizer (upstream `scrapers/hi/actions.py`, its "adopted" rule) tags every one of them `passage`. On the Mac
# replica (2026-11-24, all 68,030 stored Hawaii actions, session 2026 with its bills carried over from 2025) 2,318
# such lines carry it, and 955 (bill, chamber) pairs read as passed through them alone: 2026 HB 1800's Senate "Report
# adopted; Passed Second Reading, as amended (SD 1)" on 04-10, four days before its Senate Third Reading. So a
# committee-report adoption that is not a Third or Final Reading loses `passage`; its other labels (`reading-2`,
# `referral-committee`) stay as given. The floor passages keep it: "Report adopted; Passed Third Reading ...", "Passed
# Third Reading ...", "Passed Final Reading ...", and the adoption of a resolution ("Report and Resolution Adopted
# ...", "Resolution adopted in final form.", "Adopted with ..."), which is a resolution's passage. Nothing is added.
# The Senate writes both "Report adopted" and "Report Adopted" (stored only on Third Reading lines so far), so case is
# ignored. No stored report adoption names Final Reading; it is kept with Third Reading so a future one cannot lose a
# real passage.
_COMMITTEE_REPORT_ADOPTED = re.compile(r"^\s*Report adopted\b(?!.*\b(?:Third|Final) Reading\b)", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a committee report's adoption before Third Reading, every other label as
    given."""
    if _COMMITTEE_REPORT_ADOPTED.match(description or ""):
        return [c for c in classification if c != "passage"]
    return list(classification)


# ── Version rules (OPEN-804) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-804: Hawaii numbers a bill's drafts within each chamber: House Draft 1, 2, ... and Senate Draft 1, 2, ... A bill
# is amended in its own chamber first, then crosses over and is amended in the other ("SB145 SD2" crosses to the House
# and becomes "SB145 SD2 HD1"), then in conference ("SB145 SD2 HD1 CD1"); the Legislative Reference Bureau's Advocate's
# Guide, Part 1, "Bill Numbers" (lrb.hawaii.gov/par, 2026). So a House Draft comes before every Senate Draft on a House
# bill and after them on a Senate bill. The bill's own chamber's drafts keep note_stage's (amendment, N); the other
# chamber's come after the bill passed its own chamber, as Oklahoma's second-chamber texts do, and before the
# Conference Drafts (note_stage's "conference" rule: chamber passage, N + 0.25).
_DRAFT_RE = re.compile(r"(house|senate) draft ([1-9]\d*)")


def version_rank(note: str, origin: typing.Optional[str]) -> typing.Optional[typing.Tuple[int, float]]:
    """Hawaii's (stage, ordinal) for `version_ordering.version_sort_key`, given the version's note and the bill's
    origin chamber ("house" / "senate"), or None to keep `note_stage`'s. See the block comment above."""
    # imported at call time: version_ordering imports this module
    from openstates.utils.version_ordering import STAGE_CHAMBER_PASSAGE

    m = _DRAFT_RE.fullmatch(note.lower())
    if not m or origin not in ("house", "senate") or m.group(1) == origin:
        return None
    return (STAGE_CHAMBER_PASSAGE, 0.01 * int(m.group(2)))
