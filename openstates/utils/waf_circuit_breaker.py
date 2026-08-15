"""
Generic consecutive-WAF-block circuit breaker (OPEN-54).

Extracted from MI's own scraper-only implementation
(`openstates-scrapers/scrapers/mi/_waf_circuit_breaker.py`, OPEN-18/22/30) so both a scraper
and the archiver's fetch path (OPEN-52) can share one threshold-check/abort implementation
instead of each hand-rolling its own "abort after N consecutive blocks" logic.

Deliberately a plain function, not a stateful class: a scraper already has a natural place to
hold its own per-instance counter (`self._consecutive_waf_blocks`, unchanged from MI's original
shape so its existing tests keep passing against the same attribute), and the archiver's
module-level fetch function can hold its own module-level counter the same way -- neither needs
this function to own the state, just the increment-then-maybe-raise decision.
"""
from openstates.exceptions import ScrapeError


def raise_if_waf_block_threshold_reached(
    consecutive_blocks: int,
    max_consecutive_blocks: int,
    exc: Exception,
    scrape_label: str,
    fetch_description: str,
) -> None:
    """Raise ScrapeError if `consecutive_blocks` (the caller's own already-incremented count)
    has reached `max_consecutive_blocks`. Caller owns incrementing/resetting its own counter
    and logging -- this function only owns the shared threshold decision and error shape.
    """
    if consecutive_blocks >= max_consecutive_blocks:
        raise ScrapeError(
            f"{scrape_label} aborted: {consecutive_blocks} consecutive WAF blocks detected "
            f"{fetch_description}"
        ) from exc
