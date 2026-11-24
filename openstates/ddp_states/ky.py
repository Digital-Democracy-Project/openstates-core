"""
Kentucky's action-label rules (OPEN-845, PLAN-state-specific-handling §5.4, epic OPEN-146).

Found by the bill-stages check of Kentucky's go-live (OPEN-667) on the Mac replica (2026-11-24, session 2026RS, 12,393
stored actions on 1,737 bills). The scraper's categorizer (`scrapers/ky/bills.py`, `_action_classifiers`, first
`re.match` wins) gets two kinds of floor line wrong:

* "passed over and retained in the Orders of the Day" (67 lines), "... in the Consent Orders of the Day" (24) and
  "... retained for concurrence in House Committee Substitute (1) ..." (13) are tagged `passage`: the chamber skipped
  the bill on that day's calendar, and the rule ("passed", "passage") catches the word. They lose `passage`, as
  Nebraska's "Passed over" lines do; no label is added.
* "3rd reading, adopted 38-0 ..." (29 lines, joint and concurrent resolutions) is the chamber's roll-call vote on a
  resolution, but "3rd reading" matches first and the line gets only `reading-3`. It gets `passage`, as "3rd reading,
  passed 38-0 ..." has. SJR 62 (2026RS) shows both: passed over on 3/11, adopted by the Senate 38-0 on 3/12; with the
  first rule alone it would read as never passing the Senate.

Every other line, and every other label, is as given: the bills' real votes ("3rd reading, passed 38-0 ...", "passed
32-6", "adopted by voice vote") keep `passage`, and a simple resolution's bare "adopted 38-0" and the veto-override
"adopted 88-4" are left alone.

Not changed here, on purpose: "enrolled, signed by President of the Senate" / "... Speaker of the House" (240 lines
each) also carry `passage` although they are the presiding officers' signatures, not votes. The scraper stores every
other floor line under the bill's own chamber (the actor is read from the end of the text, and "3rd reading, passed
38-0" names none), so on the replica all 973 real passage lines sit under the origin chamber, including the 407 cast
in the second chamber. The Senate President's signature is the only line under the other chamber, so it is the only
thing that makes a Kentucky bill read as passed by both chambers: without it all 204 bills that reach "Passed both
chambers" (`validate_bill_stages`) would lose that stage. The signatures can lose `passage` once the second chamber's
lines are stored under that chamber (OPEN-845 pull request).
"""

import re
import typing

_PASSED_OVER = re.compile(r"^passed over and retained\b", re.I)
_RESOLUTION_ADOPTED = re.compile(r"^3rd reading, adopted\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a "passed over and retained ..." line, `passage` added on a "3rd reading,
    adopted ..." line, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PASSED_OVER.match(text):
        labels = [c for c in labels if c != "passage"]
    elif _RESOLUTION_ADOPTED.match(text) and "passage" not in labels:
        labels.append("passage")
    return labels
