"""
Rhode Island's action-label rule (OPEN-822, PLAN-state-specific-handling §5.4, epic OPEN-146).

The scraper's categorizer (openstates-scrapers `scrapers/ri/actions.py`) tags "Committee recommended measure be held
for further study" `committee-failure`. In Rhode Island that is the committee's usual holding step, not a defeat: the
bill stays in committee and often comes back. On the Mac replica (2026-11-24, session 2026, 14,325 actions on 3,011
bills) it is the only line carrying `committee-failure`: 2,222 lines, every one with the site's date prefix
("04/28/2026 Committee recommended ..."), and 602 of the bills held later passed a chamber.

So a "held for further study" line loses `committee-failure` and the bill reads as still in committee. No label is put
in its place: OpenStates has none that means "held" (a judgement call, put to Ramon on the pull request). Every other
label and line is left as given; "Committee recommended Substitute A be held for further study" (6 lines) carries only
`substitution` and keeps it.
"""

import re
import typing

# Searched, not anchored: the stored text starts with the date the site prints ("04/28/2026 ").
_HELD_FOR_FURTHER_STUDY = re.compile(r"\bheld for further study\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `committee-failure` on a "held for further study" line, every other label as given."""
    if "committee-failure" in classification and _HELD_FOR_FURTHER_STUDY.search(description or ""):
        return [c for c in classification if c != "committee-failure"]
    return list(classification)
