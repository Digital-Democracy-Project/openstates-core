"""
Virginia's bill-type rule (OPEN-545, PLAN-state-specific-handling contract C3, epic OPEN-536).

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
"""

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
