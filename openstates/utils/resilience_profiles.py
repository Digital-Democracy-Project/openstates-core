"""
Per-jurisdiction WAF/cookie resilience profiles (OPEN-54).

Before this, Michigan was the only jurisdiction with any WAF/cookie resilience machinery, and
every piece of it was hand-wired specifically to MI: a rate limit
(`scrapers/mi/bills.py::MI_SCRAPELIB_RPM`), a `CookieProvider` instance (`mi_cookies.py`), a
circuit breaker (`scrapers/mi/_waf_circuit_breaker.py`), and retry-exclusion/UA-rotation-opt-out
flags baked into `MIResilientScraperMixin`. The archiver's `_fetch_bytes()`
(`openstates/cli/text_extract.py`) kept its own second, independent, incomplete copy of the same
idea (`if "legislature.mi.gov" in ...`), with no circuit breaker (OPEN-52) and no MI-specific
rate limit (OPEN-53).

This module is the single source of truth both the scraper base/mixin path and the archiver's
fetch path read from, so turning the same protections on for a newly-blocked jurisdiction is a
new entry in `RESILIENCE_PROFILES` below, not another two-repo hand-wiring pass.

Reproduces MI's current real settings exactly as its profile (not re-derived/re-tuned -- see
OPEN-21/22/23's own docstrings for how those specific values were arrived at) and adds FL's real
profile (OPEN-66's backfill investigation, 2026-08-15) as the second, validated case proving a
new jurisdiction is a config-only change.
"""
import os
import typing
from dataclasses import dataclass, field

from .cookie_provider import CookieProvider
from .mi_cookies import MI_COOKIE_PROVIDER
from .fl_cookies import FL_COOKIE_PROVIDER


@dataclass(frozen=True)
class WafResilienceProfile:
    """One jurisdiction's WAF/cookie resilience configuration.

    `netloc` is the hostname `_fetch_bytes()`/`request_resiliently` match a URL against to decide
    whether this profile applies -- a jurisdiction's scraper and its archiver fetches don't
    always target the exact same domain-of-record, so this is the netloc of the specific
    WAF-guarded site (e.g. flhouse.gov, not flsenate.gov), not the jurisdiction as a whole.
    """

    name: str
    netloc: str
    cookie_provider: CookieProvider
    requests_per_minute: int
    circuit_breaker_max_consecutive_blocks: int
    retry_excluded_exceptions: tuple
    user_agent_rotation_enabled: bool


def _mi_requests_per_minute() -> int:
    # OPEN-21: MI's rate limit has been env-configurable since it was first tuned -- preserved
    # here rather than hardcoding the default, so an operator overriding MI_SCRAPELIB_RPM today
    # doesn't lose that override under the generalized profile.
    return int(os.environ.get("MI_SCRAPELIB_RPM", 10))


RESILIENCE_PROFILES: typing.Dict[str, WafResilienceProfile] = {
    "mi": WafResilienceProfile(
        name="mi",
        netloc="legislature.mi.gov",
        cookie_provider=MI_COOKIE_PROVIDER,
        requests_per_minute=_mi_requests_per_minute(),
        circuit_breaker_max_consecutive_blocks=3,
        retry_excluded_exceptions=(),  # set on the scraper via _resilience_retry_excluded_exceptions;
        # kept here at () rather than duplicating scrapelib.HTTPError/ConnectionError imports in
        # this module -- MIResilientScraperMixin still sets these directly (see its own docstring)
        # since they're a Scraper-only concept the archiver's function-based fetch has no
        # equivalent for.
        user_agent_rotation_enabled=False,
    ),
    "fl": WafResilienceProfile(
        name="fl",
        netloc="flhouse.gov",
        cookie_provider=FL_COOKIE_PROVIDER,
        # Not empirically tuned like MI's 10/min (OPEN-21) -- flhouse.gov showed no rate-limit-
        # shaped blocking in the OPEN-66 investigation, only the User-Agent bot-signature issue
        # fl_cookies.py's warm-up already fixes. Starts at the platform default's own conservative
        # sibling (MI's tuned value) as a precaution until real traffic says otherwise, not a
        # measured threshold.
        requests_per_minute=10,
        circuit_breaker_max_consecutive_blocks=3,
        retry_excluded_exceptions=(),
        user_agent_rotation_enabled=False,
    ),
}


def profile_for_netloc(netloc: str) -> typing.Optional[WafResilienceProfile]:
    """Return the resilience profile whose `netloc` matches, or None if this host has no
    profile -- the overwhelming majority of jurisdictions, by design (OPEN-54 explicitly scopes
    this to jurisdictions that have actually shown WAF-blocking symptoms, not preemptively)."""
    for profile in RESILIENCE_PROFILES.values():
        if profile.netloc == netloc:
            return profile
    return None
