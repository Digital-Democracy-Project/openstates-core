"""
Montana's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Action labels (OPEN-826) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-826: Montana's action text is the status name from api.legmt.gov (scrapers/mt/bills.py, `scrape_actions`), with
# the chamber in front ("(H) ", "(S) ", "(LC) " for the drafting office) and, for a committee's statuses, " - " and the
# committee's name after it. The scraper's categorizer (scrapers/mt/actions.py) was written for the older archive API's
# names, which carried the result ("3rd Reading Passed"). Read against every status name Montana used in 2025 (141,
# api.legmt.gov bills/v1/bills/search, session 2) and the 2027 actions on the Mac replica (13 names, 2026-11-24):
#   "(H) Introduced" / "(S) Introduced" carry no label: its pattern is "^introduced$", which the prefix defeats (1,764
#   lines in 2025; all 62 stored 2027 lines). "1st Reading" and the real "3rd Reading" carry none either, while
#   "Scheduled for 2nd / 3rd Reading" and the motions that move a bill onto a reading's calendar carry a reading.
#   "Signed by Speaker" and "Signed by President" carry `passage` (1,895 lines in 2025): the presiding officers sign the
#   enrolled bill after both chambers passed it, which is not a passage (Washington's "Speaker signed." trap).
#   "Fiscal Note Received" and the like carry `receipt`; "Tabled in Committee", the bill tabled, carries
#   `amendment-deferral`; "Died in Committee" and "Became Law Without Governor's Signature" carry nothing.
#   `failure` comes from any line containing "failed" ("2nd Reading Pass Motion Failed; 3rd Reading Vote Required",
#   "Veto Override Motion Failed"), a lost motion or amendment, not the bill. No 2025 or 2027 name contains "failed";
#   the archive API's do.
# The 2025 names carry no result: "(H) 3rd Reading" is the same text whether the bill passed or failed, and the result
# ("BILL_PASSED", "BILL_NOT_PASSED", ...) is a separate field the scraper does not keep. So no reading, committee action
# or veto override line can say `passage`, `failure` or `committee-passage` here; that needs the scraper to keep it.
#
# A name below gets exactly its labels; any other name keeps the scraper's labels, without `failure` unless it is the
# bill's own loss ("3rd Reading Failed", "Died in Process").
_PREFIX = re.compile(r"\A\((?:H|S|LC)\) ")

_LABELS = (
    (re.compile(r"introduced", re.I), ["introduction"]),
    (re.compile(r"1st reading", re.I), ["reading-1"]),
    (
        re.compile(
            r"3rd reading(?: conference committee report| free conference committee report| governor's amendments"
            r"| house amendments| senate amendments| pass consideration|; 2nd chamber vote required)?",
            re.I,
        ),
        ["reading-3"],
    ),
    # placed on a reading's calendar, not read
    (re.compile(r"scheduled for (?:2nd|3rd) reading", re.I), []),
    (re.compile(r"take from committee; place on (?:2nd|3rd) reading", re.I), []),
    (re.compile(r"taken from 3rd reading; placed on 2nd reading", re.I), []),
    (re.compile(r"taken from 2nd reading; rereferred to committee", re.I), ["referral-committee"]),
    (re.compile(r"signed by (?:speaker|president)", re.I), []),
    (re.compile(r"(?:revised )?(?:sponsor rebuttal to )?fiscal note received", re.I), []),
    (re.compile(r"tabled in committee", re.I), ["deferral"]),
    (re.compile(r"died in committee", re.I), ["committee-failure"]),
    (re.compile(r"became law without governor's signature", re.I), ["became-law"]),
)

_BILL_LOST = re.compile(r"\A(?:3rd reading failed|(?:draft )?died in process)\Z", re.I)


def _status_name(description: str) -> str:
    """The status name alone: no chamber in front, no committee after."""
    return _PREFIX.sub("", description.strip()).split(" - ", 1)[0].strip()


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels, read from its status name: see the block comment above."""
    name = _status_name(description or "")
    for pattern, labels in _LABELS:
        if pattern.fullmatch(name):
            return list(labels)
    if _BILL_LOST.match(name):
        return list(classification)
    return [c for c in classification if c != "failure"]
