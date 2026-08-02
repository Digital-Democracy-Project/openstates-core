import json
import time

import pytest

from openstates.utils.cookie_provider import (
    CookieProvider,
    WafBlockDetected,
    content_matches_block_markers,
    content_matches_fake_404_block,
)


COOKIE_NAMES = ("x-bni-fpc", "x-bni-rncf")


def _raw_cookies(ttl_seconds=86400 * 400):
    """Playwright-shaped cookie list, long-lived like the real x-bni-* cookies."""
    expires = time.time() + ttl_seconds
    return [
        {"name": "x-bni-fpc", "value": "fpc-value", "expires": expires},
        {"name": "x-bni-rncf", "value": "rncf-value", "expires": expires},
        # a cookie we don't care about should be ignored
        {"name": "ARRAffinity", "value": "irrelevant", "expires": expires},
    ]


def make_provider(tmp_path, warm_up_func=None):
    calls = {"count": 0}

    def default_warm_up(url):
        calls["count"] += 1
        return _raw_cookies()

    provider = CookieProvider(
        name="test",
        warm_up_url="https://legislature.mi.gov",
        cookie_names=COOKIE_NAMES,
        cache_path=str(tmp_path / "cookies.json"),
        warm_up_func=warm_up_func or default_warm_up,
    )
    return provider, calls


def test_cold_start_warms_up_and_caches(tmp_path):
    provider, calls = make_provider(tmp_path)

    cookies = provider.get_cookies()

    assert cookies == {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"}
    assert calls["count"] == 1
    with open(provider.cache_path) as f:
        cached = json.load(f)
    assert set(cached.keys()) == set(COOKIE_NAMES)


def test_cached_valid_jar_reused_without_relaunching(tmp_path):
    provider, calls = make_provider(tmp_path)

    first = provider.get_cookies()
    second = provider.get_cookies()

    assert first == second
    assert calls["count"] == 1  # only the cold-start warm-up, never called again


def test_expired_jar_triggers_exactly_one_rewarm(tmp_path):
    provider, calls = make_provider(tmp_path)
    provider.get_cookies()
    assert calls["count"] == 1

    # Manually stomp the cache with an already-past expiry.
    with open(provider.cache_path, "w") as f:
        json.dump(
            {
                "x-bni-fpc": {"value": "stale", "expires": time.time() - 10},
                "x-bni-rncf": {"value": "stale", "expires": time.time() - 10},
            },
            f,
        )

    cookies = provider.get_cookies()

    assert cookies == {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"}
    assert calls["count"] == 2  # exactly one fresh warm-up, not more


def test_invalidate_forces_rewarm(tmp_path):
    provider, calls = make_provider(tmp_path)
    provider.get_cookies()
    assert calls["count"] == 1

    provider.invalidate()
    provider.get_cookies()

    assert calls["count"] == 2


def test_invalidate_is_safe_when_no_cache_exists(tmp_path):
    provider, _ = make_provider(tmp_path)
    provider.invalidate()  # must not raise even though nothing has been cached yet


def test_fetch_with_retry_rewarms_once_on_block(tmp_path):
    provider, calls = make_provider(tmp_path)
    attempts = {"count": 0}

    def do_request(cookies):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise WafBlockDetected("blocked despite cached cookies")
        return f"ok with {cookies}"

    result = provider.fetch_with_retry(do_request)

    assert result == "ok with {'x-bni-fpc': 'fpc-value', 'x-bni-rncf': 'rncf-value'}"
    assert attempts["count"] == 2
    # one warm-up for the initial cold start, one more for the re-warm after the block
    assert calls["count"] == 2


def test_fetch_with_retry_propagates_second_block(tmp_path):
    provider, calls = make_provider(tmp_path)
    attempts = {"count": 0}

    def always_blocked(cookies):
        attempts["count"] += 1
        raise WafBlockDetected("still blocked")

    with pytest.raises(WafBlockDetected):
        provider.fetch_with_retry(always_blocked)

    assert attempts["count"] == 2  # initial attempt + exactly one retry, no more
    assert calls["count"] == 2  # initial warm-up + exactly one re-warm, no more


def test_fetch_with_retry_reuses_valid_cache_without_warming_up(tmp_path):
    provider, calls = make_provider(tmp_path)
    provider.get_cookies()  # cold start warm-up
    assert calls["count"] == 1

    result = provider.fetch_with_retry(lambda cookies: cookies)

    assert result == {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"}
    assert calls["count"] == 1  # no additional warm-up for a successful, unblocked request


def test_content_matches_block_markers():
    assert content_matches_block_markers(b"User validation required to continue..")
    assert content_matches_block_markers(b"Request Rejected")
    assert not content_matches_block_markers(b"<html>real bill content</html>")
    assert not content_matches_block_markers(b"")


def test_content_matches_fake_404_block():
    assert content_matches_fake_404_block(
        b"<html><body>The specified URL cannot be found.</body></html>"
    )
    # case-insensitive, like content_matches_block_markers
    assert content_matches_fake_404_block(b"THE SPECIFIED URL CANNOT BE FOUND")
    assert not content_matches_fake_404_block(
        b"<html>Senate Bill 1141 of 2026 - Michigan Legislature</html>"
    )
    assert not content_matches_fake_404_block(b"")
