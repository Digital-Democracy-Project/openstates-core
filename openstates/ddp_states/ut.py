"""
Utah's action-label rules (OPEN-547, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_utah`, BROKER-248), the label part unchanged: the broker can drop its label corrections in BROKER-490 once this
rule is live, but not its chamber correction (below).

Utah's action text starts with the chamber that acted ("House/ ", "Senate/ "), and the scraper's categorizer tags
`passage` on lines that pass nothing. On the Mac replica (2026-11-17, sessions 2026 and 2025S2):

* `passage` is the chamber's floor vote only on "<chamber>/ passed 3rd reading" (1,030 lines, all tagged) and
  "<chamber>/ passed 2nd & 3rd readings/ suspension" (230 lines, none tagged: the categorizer's rule for it is
  anchored at the start of the line and never matches). The presiding officers' "signed by Speaker" and "signed by
  President" lines (1,088) and the conference committee "Final Passage" lines (26) carry `passage` and lose it.
* `failure` is the bill's failure only on "<chamber>/ failed" (51 lines). Failed floor amendments, substitutes and
  committee "Motion to Recommend Failed" lines (58) lose it; their other labels (`amendment-failure`) stay.
* "to Lieutenant Governor" (36 lines, resolutions sent for filing) is not the governor's receipt of a bill, so it loses
  `executive-receipt`.

Not moved, because a label rule cannot hold it: the broker also reads a passage line's chamber from the text, since
the line's organization is often the chamber the bill goes to next ("House/ passed 3rd reading" is stored under the
Senate 522 times of 659). That stays in the broker until the action's organization is fixed at the source: with these
labels and no broker reader at all, 696 of Utah's 1,021 bills would read as passed by both chambers without passing
the first (`validate_bill_stages`, 2026-11-17); with the broker keeping only the chamber correction, 0 bills change.
"""

import re
import typing

_PASSAGE = re.compile(r"^(Senate|House)/ passed (2nd & )?3rd readings?\b", re.I)
_FAILURE = re.compile(r"^(Senate|House)/ failed$", re.I)
_NOT_TO_GOVERNOR = re.compile(r"to Lieutenant Governor", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `passage` on a floor passage line and nowhere else, `failure` only on "<chamber>/ failed",
    no `executive-receipt` on a line to the Lieutenant Governor, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PASSAGE.match(text):
        if "passage" not in labels:
            labels.append("passage")
    else:
        labels = [c for c in labels if c != "passage"]
    if not _FAILURE.match(text):
        labels = [c for c in labels if c != "failure"]
    if _NOT_TO_GOVERNOR.search(text):
        labels = [c for c in labels if c != "executive-receipt"]
    return labels
