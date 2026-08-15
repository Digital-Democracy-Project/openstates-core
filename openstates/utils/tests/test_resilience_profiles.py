from openstates.utils.resilience_profiles import RESILIENCE_PROFILES, profile_for_netloc


def test_mi_profile_reproduces_current_real_settings():
    """OPEN-54 AC: MI's current real resilience behavior must be reproduced exactly as its
    profile, not re-derived -- these values must match MI's original hardcoded constants
    (MAX_CONSECUTIVE_WAF_BLOCKS=3, MI_SCRAPELIB_RPM default 10, UA rotation disabled)."""
    mi = RESILIENCE_PROFILES["mi"]
    assert mi.netloc == "legislature.mi.gov"
    assert mi.circuit_breaker_max_consecutive_blocks == 3
    assert mi.requests_per_minute == 10
    assert mi.user_agent_rotation_enabled is False
    assert mi.cookie_provider.name == "mi"
    assert mi.cookie_provider.cookie_names == ("x-bni-fpc", "x-bni-rncf")


def test_fl_profile_is_a_real_second_jurisdiction_not_a_dummy():
    """OPEN-54 AC: a second jurisdiction's protections must be a config entry, not new code.
    FL is that real second profile (validated against the live WAF during the OPEN-66 backfill
    investigation, 2026-08-15), not a placeholder."""
    fl = RESILIENCE_PROFILES["fl"]
    assert fl.netloc == "flhouse.gov"
    assert fl.cookie_provider.name == "fl"
    assert fl.cookie_provider.cookie_names == ("session_cookie_mfhp",)


def test_profile_for_netloc_matches_known_host():
    assert profile_for_netloc("legislature.mi.gov").name == "mi"
    assert profile_for_netloc("flhouse.gov").name == "fl"


def test_profile_for_netloc_returns_none_for_unconfigured_host():
    # Explicitly out of scope per OPEN-54: no other jurisdiction gets a profile just because
    # this framework now exists -- only ones that have actually shown WAF-blocking symptoms.
    assert profile_for_netloc("flsenate.gov") is None
    assert profile_for_netloc("legislature.utah.gov") is None
