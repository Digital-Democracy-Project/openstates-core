"""
Florida-specific CookieProvider instance (OPEN-54, validated against the real OPEN-66 backfill
investigation, 2026-08-15).

Background: flhouse.gov sits behind an F5 BIG-IP ASM WAF. Unlike Michigan's Barracuda WAF (which
requires solving a CAPTCHA -- see ScrapeBot/`mi_cookies.py`), a live spike found flhouse.gov's WAF
doesn't challenge with a CAPTCHA at all -- it blocks on the request's own bot-detection signature.
A default Playwright warm-up (`CookieProvider._playwright_warm_up`) reproduced the exact "Request
Rejected" block real scraper traffic was hitting; the browser's own default headless User-Agent
literally announces itself as `HeadlessChrome`, and that substring alone appears to be sufficient
for this WAF to block on. Launching with Chromium's newer headless mode (`--headless=new`) and a
plain desktop Chrome User-Agent (no "Headless" substring) got real content -- including the real
`session_cookie_mfhp` cookie `scrapers/fl/bills.py`'s `_FLHouseWAFSource` already expects -- on the
first attempt, against two different bills that had 100% failed via plain `requests` moments
earlier. `navigator.webdriver` was still `True` in that same successful request, so this WAF
specifically isn't checking that signal -- only the User-Agent string.

This is behavioral evidence about this specific WAF/browser combination, not a guarantee it can
never start checking other automation signals -- if flhouse.gov's WAF behavior changes, this
warm-up needs revisiting, the same caveat MI's own cookie provider docstring makes.
"""
import os
import typing

from openstates import settings
from .cookie_provider import CookieProvider

_NON_HEADLESS_ANNOUNCING_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.7778.96 Safari/537.36"
)


def _fl_playwright_warm_up(url: str) -> typing.Tuple[typing.List[dict], str]:
    """Same shape as CookieProvider._playwright_warm_up, except the browser is launched with
    Chromium's newer headless mode and a plain desktop Chrome User-Agent -- the default warm-up
    reproduces flhouse.gov's WAF block (see module docstring); this one doesn't."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--headless=new"])
        try:
            context = browser.new_context(user_agent=_NON_HEADLESS_ANNOUNCING_UA)
            page = context.new_page()
            page.goto(url, wait_until="networkidle")
            user_agent = page.evaluate("() => navigator.userAgent")
            return context.cookies(), user_agent
        finally:
            browser.close()


FL_COOKIE_PROVIDER = CookieProvider(
    name="fl",
    warm_up_url="https://flhouse.gov/Sections/Bills/bills.aspx",
    cookie_names=("session_cookie_mfhp",),
    cache_path=os.path.join(settings.CACHE_DIR, "fl_waf_cookies.json"),
    warm_up_func=_fl_playwright_warm_up,
)
