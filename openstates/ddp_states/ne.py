"""
Nebraska's label rules (OPEN-550, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_nebraska`, BROKER-456/477), unchanged: the broker drops it in BROKER-490 once this rule is live.

Nebraska has one chamber, and the scraper's categorizer tags `passage` on lines that are not the Legislature's vote
on the bill: "President/Speaker signed" (the presiding officer signing the enrolled bill, 627 stored lines on the Mac
replica, 2026-11-17) and "Passed over" (the bill skipped on that day's agenda, 23 lines; LB 376, 109th, carries one
two months before its real vote). Only "Passed on Final Reading ..." (with or without "with Emergency Clause") is the
bill's passage, so `passage` stays on that line alone. Nothing is added: every stored "Passed on Final Reading" line
already carries `passage`.
"""

import re
import typing

_FINAL_PASSAGE = re.compile(r"^Passed on Final Reading\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `passage` only on "Passed on Final Reading" lines, every other label as given."""
    if "passage" in classification and not _FINAL_PASSAGE.match(description or ""):
        return [c for c in classification if c != "passage"]
    return list(classification)
