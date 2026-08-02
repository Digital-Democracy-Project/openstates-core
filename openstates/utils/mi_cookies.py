"""
Michigan-specific CookieProvider instance (OPEN-19).

Background: legislature.mi.gov runs Barracuda's bot-detection/WAF, which validates
clients via a JavaScript challenge a plain HTTP client (our scrapers' `requests`/scrapelib
calls) can't execute. Investigation on 2026-08-01 found that a `requests` client supplied
with just two cookies -- x-bni-fpc and x-bni-rncf -- obtained from a real Playwright page
load successfully retrieved bill pages, search-result listings, and PDF documents that
were otherwise blocked, across two independent test rounds (a full 6-cookie minimization
matrix showed the other four cookies, ARRAffinity/BNIS_*, aren't required). Both cookies
are long-lived (~13 months from creation), not session-scoped.

This is behavioral evidence about our environment, not a claim about Barracuda's internal
validation logic -- "a requests client supplied with these two cookies got through what a
bare request couldn't, in our testing so far", not "provably the only mechanism Barracuda
uses". It's possible Barracuda also ties validation to server-side state associated with
these cookies (IP, request cadence, etc.) that hasn't been stress-tested. If Barracuda
changes how their challenge/validation works, this stops working and needs revisiting --
OPEN-17/OPEN-18's fallbacks exist as defense-in-depth for exactly that case.

**Confirmed 2026-08-01, same day, a few hours after the above was written: possessing valid
x-bni-fpc/x-bni-rncf is not sufficient on its own.** After a full day of heavy automated
traffic against legislature.mi.gov (scrape attempts, archiver runs, manual testing), a
Playwright warm-up started returning a *different* cookie pair (BNIS_x-bni-jas/x-bni-ci,
session-scoped) instead of x-bni-fpc/x-bni-rncf -- and a real browser session that still had
the original, previously-working x-bni-fpc/x-bni-rncf values attached got the "Validation
request" challenge page anyway. So Barracuda tracks something beyond just these two cookies
-- most likely IP reputation and/or request-volume history -- that can override cookie-based
validation entirely once tripped. This doesn't invalidate the mechanism (it's still the best
available approach, and CookieProvider's re-warm-once-on-block behavior is exactly the right
shape of response), but it means: (a) a re-warm can legitimately fail to produce working
cookies if the whole IP is currently in a degraded-reputation state, not just when the cache
is stale, and (b) sustained high request volume against this specific site may make things
worse, not better -- there's no evidence yet on how long a reputation hit lasts or what
triggers/clears it.
"""
import os

from openstates import settings
from .cookie_provider import CookieProvider

MI_COOKIE_PROVIDER = CookieProvider(
    name="mi",
    warm_up_url="https://legislature.mi.gov",
    cookie_names=("x-bni-fpc", "x-bni-rncf"),
    cache_path=os.path.join(settings.CACHE_DIR, "mi_waf_cookies.json"),
)
