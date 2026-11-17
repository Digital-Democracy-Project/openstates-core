"""
Virginia's bill-type rule (OPEN-545, PLAN-state-specific-handling contract C3, epic OPEN-536) and its action-label
rule (OPEN-549).

Moved from the broker's read-time reading (ddp-broker-py `apps/ddp-broker/events/bill_events.py`,
`read_classification`, BROKER-478), with the scraper's title check first (below): the broker drops its copy once this
rule is live and Virginia's stored history is relabelled.

Until OPEN-545 Virginia's scraper labelled every item `bill`, resolutions included: 1,579 stored items on the Mac
replica (2026-11-17; HR 692, SR 271, HJ 322, SJ 294, across 2026, 2026S1 and 2027). The bill number says what it is:
HB/SB stay `bill`, HR/SR are `resolution`, HJ/SJ `joint resolution`. Only the `bill` label is rewritten, and a title
naming a constitutional amendment comes first, as in the scraper: such an item is a `constitutional amendment` whatever
its number (20 stored, 18 in 2026 and 2 in 2027, all already labelled so).

The scraper (openstates-scrapers `scrapers/va/bills.py`, `classify_bill`, upstream's rule since #5909) labels new
scrapes the same way; this module is what `os-text-extract relabel va` applies to the stored history, so a re-scrape
of a relabelled bill keeps its label. Keep the two equal.

Virginia's action-label rule (OPEN-549) is `classify_action` below: the broker's read-time reader (ddp-broker-py
`apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`, `_read_virginia`, BROKER-248 and BROKER-481), unchanged.
It only takes a label off, so nothing of `_read_virginia` has to stay in the broker (BROKER-490) once this rule is live
and the stored history is relabelled. On the Mac replica (2026-11-17, sessions 2026, 2026S1 and 2027):

* "Governor's recommendation adopted" (138 lines, all stored under the executive with `executive-signature`) is the
  legislature accepting the governor's amendments after both chambers concurred ("House concurred in Governor's
  recommendation (65-Y 35-N 0-A)"); the bill is signed later, by "Approved by Governor-Chapter ..." (HB 891, 2026).
  It loses `executive-signature`, or the bill reads as signed into law before it is.
* "Subcommittee failed to recommend ..." (11 lines with `committee-failure`, all House) is a subcommittee's
  recommendation to its committee, not the committee's decision: HB 625 and HB 871 (2026) were reconsidered the same
  day and reported. It loses `committee-failure`. The committee's own "Failed to report (defeated) in ..." keeps it.

Not touched here: the other Virginia lines the broker reads as they are (for example "Signed by President" and "Bill
text as passed House and Senate", both stored with `passage`).
"""

import re
import typing

_TYPE_BY_PREFIX = {
    "HR": "resolution",
    "SR": "resolution",
    "HJ": "joint resolution",
    "SJ": "joint resolution",
}


def classify_bill(
    *,
    identifier: typing.Optional[str],
    title: typing.Optional[str],
    classification: typing.Sequence[str],
) -> typing.List[str]:
    """A bill's labels: `bill` read from the title, then the bill number ("HR 1" -> `resolution`, "HJ 10" -> `joint
    resolution`), every other label as given. The scraper's order, so the two give the same label on any input."""
    if "constitutional amendment" in (title or "").lower():
        read = "constitutional amendment"
    else:
        read = _TYPE_BY_PREFIX.get("".join((identifier or "").split())[:2].upper())
    if read is None:
        return list(classification)
    return [read if c == "bill" else c for c in classification]


# The broker's `_read_virginia` tests: `text.lower().startswith("governor's recommendation adopted")`, and
# `^Subcommittee failed to recommend\b`, case-insensitive.
_GOVERNOR_RECOMMENDATION_ADOPTED = re.compile(r"^Governor's recommendation adopted", re.I)
_SUBCOMMITTEE_FAILED = re.compile(r"^Subcommittee failed to recommend\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `executive-signature` on "Governor's recommendation adopted", no `committee-failure` on
    a subcommittee's "failed to recommend ...", every other label as given. Reads the text only, as the broker did."""
    text = description or ""
    labels = list(classification)
    if _GOVERNOR_RECOMMENDATION_ADOPTED.match(text):
        labels = [c for c in labels if c != "executive-signature"]
    if _SUBCOMMITTEE_FAILED.match(text):
        labels = [c for c in labels if c != "committee-failure"]
    return labels
