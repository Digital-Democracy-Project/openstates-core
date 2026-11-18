"""
Arizona's action-label rule (OPEN-552, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_arizona`, BROKER-481), unchanged: the broker drops it in BROKER-490 once this rule is live. `_read_arizona` only
removes labels, so nothing else of it has to stay in the broker.

Every Arizona failure line is the bare word "FAILED", with no motion named. The scraper labels it `failure` (with
`reading-3` when it follows a third reading). It follows committee do-pass motions, second readings and amendment
votes as well as floor votes, and a third-reading "FAILED" is often followed by the bill's own passage after a motion
to reconsider: HB 2946 (2026) failed its third reading 26-30 on 3/3 (azleg.gov), the House reconsidered it the same
evening ("PASSED"), passed it on 3/11, and the governor signed it on 6/4. The line cannot say what failed, or whether it
stays failed, so it is never read as the bill's failure. On the Mac replica (2026-11-17, session 57th-2nd-regular) 175
lines carry it: 107 `failure, reading-3` and 68 `failure`. None carries `committee-failure` (the broker reader removes
both; so does this).

Unlike the other states' rules, this label is also read by the broker's scorecard rule (BROKER-68,
`motion_eligibility._own_record_says_failed`): an Arizona vote whose text says "failed to pass" is kept off scorecards
only while a same-day action in its chamber carries `failure`, and in Arizona that action is always one of these
lines. Without the label all 141 such votes on the replica count. The scraper labels every "failed to pass" vote
`passage`, including Committee of the Whole motions to amend (HB 2805, HCR 2001) and motions to reconsider (SB 1100),
not only third readings (HB 2918, SB 1093). So this rule must not reach production before those votes are dealt with:
see OPEN-552.
"""

import re
import typing

_BARE_FAILED = re.compile(r"^\s*FAILED\.?\s*$", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `failure` or `committee-failure` on the bare "FAILED" line, every other label as
    given."""
    if _BARE_FAILED.match(description or ""):
        return [c for c in classification if c not in ("failure", "committee-failure")]
    return list(classification)
