"""
Generic disk-cached cookie provider for legislature sites that sit behind a
JavaScript-challenge WAF (bot-detection/CAPTCHA layer) a plain HTTP client can't pass.

The pattern (see openstates/utils/mi_cookies.py for the concrete Michigan/Barracuda
instance, OPEN-19): launch a real browser (Playwright) once to pass the WAF's challenge,
extract the cookies that let a plain `requests`/scrapelib client back in, and cache them
to disk -- keyed by their own real expiry -- so the browser is a rare cookie-acquisition
step, not the per-request transport. A scraper run is a fresh process each time, so the
cache has to be durable on disk (not just in-memory) for "don't re-warm on every
invocation" to actually hold across runs.

Playwright itself is imported lazily, inside the warm-up path only, so importing this
module (or getting a cache hit) never requires playwright to be installed/importable --
most scrape runs should never touch it at all.
"""
import json
import logging
import os
import time
import typing

logger = logging.getLogger("openstates")


class WafBlockDetected(Exception):
    """Raised by a do_request callable to signal a detected WAF block (connection reset,
    or a response that matches a known block-page heuristic) -- distinct from a plain
    request exception so CookieProvider.fetch_with_retry knows to invalidate and re-warm
    rather than simply letting the error propagate."""


# Markers found in known WAF/bot-detection challenge and block pages (HTTP 200, but not
# the real content) across multiple jurisdictions' scrapers/archiver. Shared here instead
# of duplicated per call site so new markers only need to be added once.
BLOCK_PAGE_MARKERS = (
    b"user validation required",
    b"captcha_resp",
    b"pardon the interruption",
    b"request rejected",
    b"checking your browser before accessing",
)


def content_matches_block_markers(data: bytes) -> bool:
    """True if `data` looks like a known WAF challenge/block page rather than real content."""
    if not data:
        return False
    sniff = data[:2048].lower()
    return any(marker in sniff for marker in BLOCK_PAGE_MARKERS)


# A Playwright cookie with no real expiry (session cookie, `expires` missing or <= 0) is
# cached for this long before being treated as stale -- long enough to avoid re-warming on
# every single invocation, short enough that a genuinely session-scoped cookie doesn't get
# treated as durable indefinitely.
_DEFAULT_SESSION_COOKIE_TTL_SECONDS = 3600


class CookieProvider:
    """Caches a small set of named cookies (obtained by warming up a real browser against
    `warm_up_url`) to a JSON file at `cache_path`, keyed by each cookie's own expiry.

    warm_up_func, if given, overrides the default Playwright-based warm-up -- primarily so
    tests can supply a fake without needing a real browser installed.
    """

    def __init__(
        self,
        name: str,
        warm_up_url: str,
        cookie_names: typing.Sequence[str],
        cache_path: str,
        warm_up_func: typing.Optional[typing.Callable[[str], typing.List[dict]]] = None,
    ):
        self.name = name
        self.warm_up_url = warm_up_url
        self.cookie_names = tuple(cookie_names)
        self.cache_path = cache_path
        self._warm_up_func = warm_up_func or self._playwright_warm_up

    def get_cookies(self) -> typing.Dict[str, str]:
        """Return the cached cookie dict, warming up a fresh one if the cache is missing,
        unreadable, or any required cookie has expired."""
        cached = self._read_cache()
        if cached is not None:
            return cached
        return self._warm_up_and_cache()

    def invalidate(self) -> None:
        """Delete the on-disk cache, forcing the next get_cookies() call to re-warm."""
        try:
            os.remove(self.cache_path)
        except FileNotFoundError:
            pass

    def fetch_with_retry(self, do_request: typing.Callable[[typing.Dict[str, str]], typing.Any]) -> typing.Any:
        """Run do_request(cookies) with the cached cookie set.

        do_request should raise WafBlockDetected if the attempt was blocked (a connection
        reset, or a response matching a known block-page heuristic). On that first
        failure, the cache is invalidated and re-warmed, and do_request is retried exactly
        once more with the fresh cookies -- a second WafBlockDetected (or any other
        exception) propagates normally rather than retrying again, per OPEN-19's "avoid
        warming up on every transient block" requirement.
        """
        cookies = self.get_cookies()
        try:
            return do_request(cookies)
        except WafBlockDetected:
            logger.warning(
                f"{self.name}: block detected despite cached cookies; "
                "invalidating cache and re-warming once"
            )
            self.invalidate()
            cookies = self.get_cookies()
            return do_request(cookies)

    def _read_cache(self) -> typing.Optional[typing.Dict[str, str]]:
        try:
            with open(self.cache_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        now = time.time()
        cookies = {}
        for cookie_name in self.cookie_names:
            entry = data.get(cookie_name)
            if not entry or entry.get("expires", 0) <= now:
                # Any missing/expired required cookie invalidates the whole cached jar --
                # a partial cookie set isn't known to be sufficient (OPEN-19's own
                # investigation only validated the full required set together).
                return None
            cookies[cookie_name] = entry["value"]
        return cookies

    def _warm_up_and_cache(self) -> typing.Dict[str, str]:
        logger.info(
            f"{self.name}: cookie cache missing/expired, warming up against {self.warm_up_url}"
        )
        raw_cookies = self._warm_up_func(self.warm_up_url)

        now = time.time()
        data = {}
        cookies = {}
        for c in raw_cookies:
            if c.get("name") not in self.cookie_names:
                continue
            expires = c.get("expires") or 0
            if expires <= 0:
                expires = now + _DEFAULT_SESSION_COOKIE_TTL_SECONDS
            data[c["name"]] = {"value": c["value"], "expires": expires}
            cookies[c["name"]] = c["value"]

        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(data, f)
        return cookies

    @staticmethod
    def _playwright_warm_up(url: str) -> typing.List[dict]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="networkidle")
                return context.cookies()
            finally:
                browser.close()
