import json
import time

import pytest

from openstates.utils.cookie_provider import (
    CookieProvider,
    BLOCK_PAGE_MARKERS,
    WafBlockDetected,
    content_matches_block_markers,
    content_matches_fake_404_block,
    matched_block_marker,
)


COOKIE_NAMES = ("x-bni-fpc", "x-bni-rncf")
DEFAULT_USER_AGENT = "Mozilla/5.0 (Warmup Browser) Chrome/999"


def _raw_cookies(ttl_seconds=86400 * 400):
    """Playwright-shaped cookie list, long-lived like the real x-bni-* cookies."""
    expires = time.time() + ttl_seconds
    return [
        {"name": "x-bni-fpc", "value": "fpc-value", "expires": expires},
        {"name": "x-bni-rncf", "value": "rncf-value", "expires": expires},
        # a cookie we don't care about should be ignored
        {"name": "ARRAffinity", "value": "irrelevant", "expires": expires},
    ]


def make_provider(tmp_path, warm_up_func=None, user_agent=DEFAULT_USER_AGENT):
    calls = {"count": 0}

    def default_warm_up(url):
        calls["count"] += 1
        return _raw_cookies(), user_agent

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
    assert set(cached.keys()) == set(COOKIE_NAMES) | {"_meta"}
    assert cached["_meta"]["user_agent"] == DEFAULT_USER_AGENT


def test_get_user_agent_returns_value_captured_with_current_cookies(tmp_path):
    provider, calls = make_provider(tmp_path, user_agent="Mozilla/5.0 (Real Chromium)")

    user_agent = provider.get_user_agent()

    assert user_agent == "Mozilla/5.0 (Real Chromium)"
    assert calls["count"] == 1  # one warm-up, shared with get_cookies()'s own cache


def test_get_cookies_and_get_user_agent_share_one_warm_up(tmp_path):
    provider, calls = make_provider(tmp_path)

    cookies = provider.get_cookies()
    user_agent = provider.get_user_agent()

    assert cookies == {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"}
    assert user_agent == DEFAULT_USER_AGENT
    # the second call (get_user_agent) must hit the cache the first call wrote, not
    # trigger an independent second warm-up (AC1: never re-derived independently)
    assert calls["count"] == 1


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
                "_meta": {"user_agent": "stale-agent"},
            },
            f,
        )

    cookies = provider.get_cookies()

    assert cookies == {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"}
    assert calls["count"] == 2  # exactly one fresh warm-up, not more


def test_old_format_cache_missing_user_agent_triggers_exactly_one_rewarm(tmp_path):
    """A cache file written before OPEN-23 (or otherwise missing the captured UA) must not
    silently hand back cookies with no matching user_agent -- it's treated like a
    missing/expired cookie and rewarmed, once, together with a freshly captured UA."""
    provider, calls = make_provider(tmp_path)

    with open(provider.cache_path, "w") as f:
        json.dump(
            {
                "x-bni-fpc": {"value": "old-format", "expires": time.time() + 86400},
                "x-bni-rncf": {"value": "old-format", "expires": time.time() + 86400},
                # no "_meta" key at all -- the pre-OPEN-23 cache shape
            },
            f,
        )

    cookies, user_agent = provider._get_session()

    assert cookies == {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"}
    assert user_agent == DEFAULT_USER_AGENT
    assert calls["count"] == 1  # exactly one rewarm to backfill the missing UA


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

    def do_request(cookies, user_agent):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise WafBlockDetected("blocked despite cached cookies")
        return f"ok with {cookies} agent={user_agent}"

    result = provider.fetch_with_retry(do_request)

    assert result == (
        "ok with {'x-bni-fpc': 'fpc-value', 'x-bni-rncf': 'rncf-value'} "
        f"agent={DEFAULT_USER_AGENT}"
    )
    assert attempts["count"] == 2
    # one warm-up for the initial cold start, one more for the re-warm after the block
    assert calls["count"] == 2


def test_fetch_with_retry_propagates_second_block(tmp_path):
    provider, calls = make_provider(tmp_path)
    attempts = {"count": 0}

    def always_blocked(cookies, user_agent):
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

    result = provider.fetch_with_retry(lambda cookies, user_agent: (cookies, user_agent))

    assert result == (
        {"x-bni-fpc": "fpc-value", "x-bni-rncf": "rncf-value"},
        DEFAULT_USER_AGENT,
    )
    assert calls["count"] == 1  # no additional warm-up for a successful, unblocked request


def test_fetch_with_retry_rewarm_pairs_fresh_cookies_with_fresh_user_agent(tmp_path):
    """A re-warm after a detected block must refresh cookies and user_agent together --
    the retried do_request() must never see the new cookies paired with the stale UA from
    the original (pre-block) warm-up."""
    user_agents = iter(["agent-v1", "agent-v2"])
    calls = {"count": 0}

    def warm_up(url):
        calls["count"] += 1
        return _raw_cookies(), next(user_agents)

    provider, _unused_calls = make_provider(tmp_path, warm_up_func=warm_up)
    seen = []

    def do_request(cookies, user_agent):
        seen.append(user_agent)
        if len(seen) == 1:
            raise WafBlockDetected("blocked despite cached cookies")
        return "ok"

    result = provider.fetch_with_retry(do_request)

    assert result == "ok"
    assert seen == ["agent-v1", "agent-v2"]
    assert calls["count"] == 2


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


# --- OPEN-334: say what a block looked like, and what the warm-up page received ---


@pytest.mark.parametrize("marker", [m.decode() for m in BLOCK_PAGE_MARKERS])
def test_matched_block_marker_names_the_marker_case_insensitively(marker):
    page = b"<html><title>Challenge</title>" + marker.upper().encode() + b"</html>"
    assert matched_block_marker(page) == marker
    assert content_matches_block_markers(page) is True


def test_matched_block_marker_is_none_for_real_content_and_empty():
    assert matched_block_marker(b"<html>Bill HB 1 and its actions</html>") is None
    assert matched_block_marker(b"") is None
    assert content_matches_block_markers(b"") is False


def test_matched_block_marker_only_looks_at_the_first_2kib():
    assert matched_block_marker(b"x" * 2048 + b"captcha_resp") is None


def test_fetch_with_retry_logs_what_the_block_looked_like(tmp_path, caplog):
    provider, _ = make_provider(tmp_path)
    attempts = {"n": 0}

    def do_request(cookies, user_agent):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise WafBlockDetected("matched 'captcha_resp' (HTTP 200) on /Bills/Bill")
        return "ok"

    with caplog.at_level("WARNING", logger="openstates"):
        assert provider.fetch_with_retry(do_request) == "ok"
    assert "block detected despite cached cookies (matched 'captcha_resp' (HTTP 200) on /Bills/Bill)" in caplog.text


def test_warm_up_logs_cookie_names_and_which_required_ones_were_not_issued(tmp_path, caplog):
    def warm_up(url):
        return [
            {"name": "ARRAffinity", "value": "secret-affinity", "expires": 0},
            {"name": ".AspNetCore.Session", "value": "secret-session", "expires": 0},
        ], DEFAULT_USER_AGENT

    provider, _ = make_provider(tmp_path, warm_up_func=warm_up)
    with caplog.at_level("INFO", logger="openstates"):
        provider.get_cookies()
    assert "warm-up page set cookies ['.AspNetCore.Session', 'ARRAffinity']" in caplog.text
    assert "required cookies not issued: ['x-bni-fpc', 'x-bni-rncf']" in caplog.text
    assert "secret-" not in caplog.text  # names only, never values


def test_warm_up_log_has_no_not_issued_clause_when_all_required_cookies_arrive(tmp_path, caplog):
    provider, _ = make_provider(tmp_path)
    with caplog.at_level("INFO", logger="openstates"):
        provider.get_cookies()
    assert "warm-up page set cookies" in caplog.text
    assert "not issued" not in caplog.text
    assert "fpc-value" not in caplog.text


def test_fetch_with_retry_caps_the_block_text_it_logs(tmp_path, caplog):
    provider, _ = make_provider(tmp_path)
    attempts = {"n": 0}

    def do_request(cookies, user_agent):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise WafBlockDetected("x" * 500)
        return "ok"

    with caplog.at_level("WARNING", logger="openstates"):
        provider.fetch_with_retry(do_request)
    assert "x" * 200 in caplog.text and "x" * 201 not in caplog.text
