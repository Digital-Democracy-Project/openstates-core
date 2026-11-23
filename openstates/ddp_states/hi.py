"""
Hawaii's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


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
