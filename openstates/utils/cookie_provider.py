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


# legislature.mi.gov's WAF can also block a request behind a genuine HTTP 404 status, serving
# its own site-styled "The specified URL cannot be found" error page instead of the real
# content (OPEN-18) -- unlike BLOCK_PAGE_MARKERS above, this isn't a 200-status challenge page,
# so it's only meaningful to check from inside a 404/HTTPError handler, not on every response.
# Kept as its own marker set/function rather than folded into BLOCK_PAGE_MARKERS for that reason.
FAKE_404_BLOCK_MARKERS = (b"the specified url cannot be found",)


def content_matches_fake_404_block(data: bytes) -> bool:
    """True if `data` looks like legislature.mi.gov's generic 'URL cannot be found' error page
    -- a WAF block served with a genuine 404 status, not a real dead link (OPEN-18)."""
    if not data:
        return False
    sniff = data[:2048].lower()
    return any(marker in sniff for marker in FAKE_404_BLOCK_MARKERS)


# A Playwright cookie with no real expiry (session cookie, `expires` missing or <= 0) is
# cached for this long before being treated as stale -- long enough to avoid re-warming on
# every single invocation, short enough that a genuinely session-scoped cookie doesn't get
# treated as durable indefinitely.
_DEFAULT_SESSION_COOKIE_TTL_SECONDS = 3600

# Reserved top-level key in the on-disk cache JSON for warm-up metadata that isn't itself a
# named cookie -- currently just the real User-Agent the warm-up browser used to obtain the
# cached cookies (OPEN-23). Kept alongside the cookie entries (not a separate cache file) so
# one warm-up populates both together and a caller can never end up pairing a cached cookie
# set with an independently-guessed UA.
_META_KEY = "_meta"


class CookieProvider:
    """Caches a small set of named cookies (obtained by warming up a real browser against
    `warm_up_url`) to a JSON file at `cache_path`, keyed by each cookie's own expiry. Also
    caches the real User-Agent that warm-up browser used (OPEN-23) -- see get_user_agent().

    warm_up_func, if given, overrides the default Playwright-based warm-up -- primarily so
    tests can supply a fake without needing a real browser installed. It must return
    (raw_cookies, user_agent): raw_cookies in Playwright's own cookie-dict shape, user_agent
    the real UA string the same browser/page sent while obtaining them.
    """

    def __init__(
        self,
        name: str,
        warm_up_url: str,
        cookie_names: typing.Sequence[str],
        cache_path: str,
        warm_up_func: typing.Optional[
            typing.Callable[[str], typing.Tuple[typing.List[dict], str]]
        ] = None,
    ):
        self.name = name
        self.warm_up_url = warm_up_url
        self.cookie_names = tuple(cookie_names)
        self.cache_path = cache_path
        self._warm_up_func = warm_up_func or self._playwright_warm_up

    def get_cookies(self) -> typing.Dict[str, str]:
        """Return the cached cookie dict, warming up a fresh one if the cache is missing,
        unreadable, or any required cookie has expired."""
        cookies, _user_agent = self._get_session()
        return cookies

    def get_user_agent(self) -> str:
        """Return the real User-Agent that warmed up the currently-cached cookie pair --
        sourced from the exact same warm-up as get_cookies(), never re-derived
        independently. Warms up a fresh session (cookies + UA together) under the same
        conditions get_cookies() would."""
        _cookies, user_agent = self._get_session()
        return user_agent

    def invalidate(self) -> None:
        """Delete the on-disk cache, forcing the next get_cookies()/get_user_agent() call
        to re-warm."""
        try:
            os.remove(self.cache_path)
        except FileNotFoundError:
            pass

    def fetch_with_retry(
        self,
        do_request: typing.Callable[[typing.Dict[str, str], str], typing.Any],
    ) -> typing.Any:
        """Run do_request(cookies, user_agent) with the cached cookie set and the User-Agent
        that warmed it up -- always a matched pair, sourced from the same cache entry/warm-up.

        do_request should raise WafBlockDetected if the attempt was blocked (a connection
        reset, or a response matching a known block-page heuristic). On that first
        failure, the cache is invalidated and re-warmed, and do_request is retried exactly
        once more with the fresh cookies and their matching fresh User-Agent -- a second
        WafBlockDetected (or any other exception) propagates normally rather than retrying
        again, per OPEN-19's "avoid warming up on every transient block" requirement. A
        re-warm can legitimately yield a different real UA than before (a different
        Chromium build, say), so the retry re-fetches both together rather than reusing the
        first attempt's UA against the new cookies.

        Goes through the public get_cookies()/get_user_agent() (rather than the private
        _get_session() directly) so a caller/test that overrides those two methods (e.g. to
        stub out the real warm-up) still sees fetch_with_retry's real invalidate-and-retry
        orchestration -- get_cookies() and get_user_agent() share one underlying cache
        read/warm-up in the real implementation either way, so this is never two
        independent derivations of the pair.
        """
        cookies = self.get_cookies()
        user_agent = self.get_user_agent()
        try:
            return do_request(cookies, user_agent)
        except WafBlockDetected:
            logger.warning(
                f"{self.name}: block detected despite cached cookies; "
                "invalidating cache and re-warming once"
            )
            self.invalidate()
            cookies = self.get_cookies()
            user_agent = self.get_user_agent()
            return do_request(cookies, user_agent)

    def _get_session(self) -> typing.Tuple[typing.Dict[str, str], str]:
        """Return (cookies, user_agent) together -- always from the same cache entry/warm-up,
        warming up a fresh one if the cache is missing, unreadable, any required cookie has
        expired, or the cache predates OPEN-23 and has no captured user_agent at all."""
        data = self._read_cache()
        if data is not None:
            cookies = {name: data[name]["value"] for name in self.cookie_names}
            return cookies, data[_META_KEY]["user_agent"]
        return self._warm_up_and_cache()

    def _read_cache(self) -> typing.Optional[dict]:
        try:
            with open(self.cache_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        meta = data.get(_META_KEY)
        if not meta or not meta.get("user_agent"):
            # Missing/old-format cache (e.g. written before OPEN-23) has no captured UA --
            # treat the whole jar as invalid so it gets rewarmed together with a freshly
            # paired UA, rather than silently returning cookies with no matching UA.
            return None

        now = time.time()
        for cookie_name in self.cookie_names:
            entry = data.get(cookie_name)
            if not entry or entry.get("expires", 0) <= now:
                # Any missing/expired required cookie invalidates the whole cached jar --
                # a partial cookie set isn't known to be sufficient (OPEN-19's own
                # investigation only validated the full required set together).
                return None
        return data

    def _warm_up_and_cache(self) -> typing.Tuple[typing.Dict[str, str], str]:
        logger.info(
            f"{self.name}: cookie cache missing/expired, warming up against {self.warm_up_url}"
        )
        raw_cookies, user_agent = self._warm_up_func(self.warm_up_url)

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
        data[_META_KEY] = {"user_agent": user_agent}

        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(data, f)
        return cookies, user_agent

    @staticmethod
    def _playwright_warm_up(url: str) -> typing.Tuple[typing.List[dict], str]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="networkidle")
                # Real UA the same page/browser sent while obtaining the cookies (OPEN-23) --
                # captured here, not re-derived independently by any caller.
                user_agent = page.evaluate("() => navigator.userAgent")
                return context.cookies(), user_agent
            finally:
                browser.close()
