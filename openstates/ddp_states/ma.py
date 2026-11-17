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
