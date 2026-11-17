"""
Michigan's action-label rule (OPEN-548, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_michigan`, BROKER-248), unchanged: the broker drops it in BROKER-490 once this rule is live. `_read_michigan`
only removes a label, so nothing else of it has to stay in the broker.

Each chamber's journal records the other chamber's passage as a message: the Senate's "PASSED BY HOUSE WITH IMMEDIATE
EFFECT" (and its variants: "WITH SUBSTITUTE (H-5)", "WITH AMENDMENT(S)", "BY 2/3 VOTE WITHOUT AMENDMENT") is stored
under the Senate, after the House's own roll call ("passed; given immediate effect Roll Call #4 ...", stored under
the House). The message is not a vote. Labelled `passage`, it reads as the receiving chamber passing the bill, so a
bill passed by one chamber reads as passed by both, and its passage date is the day the message was read.

The scraper has not labelled these lines since OPEN-344 (`mi/bills.py`, `categorize_action`), but stored history
still has them: on the Mac replica (2026-11-17, session 2025-2026) 610 such lines carry `passage` (dated 2025-02-04
to 2026-10-01) and 2 newer ones do not. The roll-call lines that start "PASSED BY 2/3 VOTE ROLL CALL # ..." (SB 304,
SB 602, SB 704) are a chamber's own vote and keep `passage`; the scraper keeps it on them too.
"""

import re
import typing

_CHAMBER_MESSAGE = re.compile(r"^PASSED BY (HOUSE|SENATE)\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on the other chamber's "PASSED BY HOUSE/SENATE ..." message, every other
    label as given."""
    if "passage" in classification and _CHAMBER_MESSAGE.match(description or ""):
        return [c for c in classification if c != "passage"]
    return list(classification)
