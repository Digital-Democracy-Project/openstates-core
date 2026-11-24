"""
Wyoming's action-label rules (OPEN-821, PLAN-state-specific-handling §5.4, epic OPEN-146).

Found by the bill-stages check of Wyoming's go-live (OPEN-662) on the Mac replica (2026-11-24, session 2026, 3,713
stored actions on 335 bills). Two of the scraper's labels (`scrapers/wy/actions.py`) are wrong:

* "H Failed Introduction 39-23-0-0-0" / "S Failed Introduction ..." (60 House and 32 Senate lines, 92 bills) carries
  no label. In the 2026 budget session a bill that is not the budget needs two thirds of the chamber to be introduced,
  and this vote ends it (HB 50: nothing follows). Without a label the bill reads as Introduced, so it gets `failure`.
  A chamber can still revive such a bill under its rules (HB 116 (2026): "H Pursuant to HR 12-11 (a) Passed by Roll
  Call", then introduced and passed by the House); the later lines carry their own labels, so it reads on from there.
* "Bill Number Assigned" (335 lines, one per bill, stored under the executive: the Legislative Service Office numbering
  the draft before a chamber receives it) is labelled `reading-1`. It is not a first reading, so it loses that label.

"H Did not Consider for Introduction" (48 lines, `deferral`) also ends a bill in a budget session, but no vote took
place; it is left as the scraper labels it (a judgement call listed in the OPEN-821 pull request). Every other line,
and every other label, is as given.
"""

import re
import typing

_FAILED_INTRODUCTION = re.compile(r"^[HS] Failed Introduction\b", re.I)
_NUMBER_ASSIGNED = re.compile(r"^Bill Number Assigned\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `failure` added on a chamber's "Failed Introduction" vote, no `reading-1` on "Bill Number
    Assigned", every other label as given."""
    text = description or ""
    labels = list(classification)
    if _FAILED_INTRODUCTION.match(text) and "failure" not in labels:
        labels.append("failure")
    if _NUMBER_ASSIGNED.match(text):
        labels = [c for c in labels if c != "reading-1"]
    return labels
