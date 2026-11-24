"""
North Dakota's action-label rules (OPEN-833, PLAN-state-specific-handling §5.4, epic OPEN-146).

Found by the bill-stages check of North Dakota's go-live (OPEN-663) on the Mac replica (2026-11-24, sessions 69, 69S1
and 69S2, 15,495 stored actions on 1,106 bills). In North Dakota a chamber votes on a bill at Second Reading:
"Second reading, passed, yeas 46 nays 0" carries `passage, reading-2`. Its defeat carries `reading-2` alone, so a bill
killed on the floor never reads as failed. Three forms are stored, all only in this shape:

* "Second reading, failed to pass, yeas 10 nays 36" (400 lines: bills)
* "Second reading, failed to adopt" / "... failed to adopt yeas 15 nays 77" (30 lines: resolutions)
* "Second reading, failed, lacks constitutional majority yeas 47 nays 37" (8 lines: more yeas than nays, but fewer
  than the constitution requires)

Each gains `failure`; `reading-2` and any other label stay. A chamber that reconsiders a failed bill and passes it
prints a later "Second reading, passed ..." line that carries its own `passage`, so the bill reads on from there.
A resolution's adoption ("Second reading, adopted ...") carries no `passage` (the scraper's labels, unchanged here),
so the one stored resolution that failed and was then reconsidered and adopted, SCR 4008 (69), still reads as failed.
The other failed motions are left as the scraper labels them: "Amendment failed" (an amendment, `amendment-failure`),
"Motion to reconsider failed" (the bill had already passed or failed), "Emergency clause failed" (the bill can still
pass without it) and "Division A lost" (one part of a divided question).
"""

import re
import typing

# The three stored forms, whole line, with or without the roll call; any other wording is looked at before it is
# labelled.
_FLOOR_DEFEAT = re.compile(
    r"^\s*Second reading, failed(?: to (?:pass|adopt)|, lacks constitutional majority)(?:,? yeas \d+ nays \d+)?\s*$",
    re.I,
)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `failure` added on a Second Reading defeat, every other label as given."""
    labels = list(classification)
    if _FLOOR_DEFEAT.match(description or "") and "failure" not in labels:
        labels.append("failure")
    return labels
