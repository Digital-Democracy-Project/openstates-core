"""OPEN-555: per-state values read from ddp-open-states' jurisdictions.yaml, falling back to the old constant."""
import importlib

import pytest

from openstates.utils import ddp_manifest, resilience_profiles
from openstates.utils.ddp_manifest import manifest_value

MANIFEST = """
mi:
  waf: {profile: cookie_provider, requests_per_minute: 10, max_consecutive_blocks: 3}
fl:
  waf: {profile: cookie_provider, requests_per_minute: 10, max_consecutive_blocks: 3}
ut:
  waf: {profile: none, requests_per_minute: null, max_consecutive_blocks: null}
"""


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    def write(text):
        path = tmp_path / "jurisdictions.yaml"
        path.write_text(text)
        monkeypatch.setenv("DDP_JURISDICTIONS_MANIFEST", str(path))
        ddp_manifest._cache.clear()
        return path

    monkeypatch.delenv("MI_SCRAPELIB_RPM", raising=False)
    yield write
    ddp_manifest._cache.clear()


def test_reads_the_manifest_value_not_the_fallback(manifest):
    manifest("mi:\n  waf: {requests_per_minute: 7}\n")
    assert manifest_value("mi", "waf.requests_per_minute", 10) == 7
    assert manifest_value("MI", "waf.requests_per_minute", 10) == 7


@pytest.mark.parametrize(
    "text",
    [
        "fl:\n  waf: {}\n",  # field absent (a manifest from before OPEN-555)
        "fl:\n  waf: {requests_per_minute: null}\n",  # null: not set for this state
        "fl:\n  waf: {requests_per_minute: ten}\n",  # wrong type
        "fl:\n  waf: {requests_per_minute: true}\n",  # a bool is not a number
        "fl:\n  waf: 3\n",  # block is not a mapping
        "ut:\n  waf: {requests_per_minute: 7}\n",  # state has no entry
        "- not a mapping\n",
        "fl: [unclosed\n",  # not YAML
    ],
)
def test_falls_back_to_the_constant(manifest, text):
    manifest(text)
    assert manifest_value("fl", "waf.requests_per_minute", 10) == 10


def test_missing_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("DDP_JURISDICTIONS_MANIFEST", str(tmp_path / "nope.yaml"))
    assert manifest_value("fl", "waf.requests_per_minute", 10) == 10


def test_a_changed_file_is_read_again(manifest):
    path = manifest("nc:\n  votes: {bare_key_session: '2025'}\n")
    assert manifest_value("nc", "votes.bare_key_session", "2025") == "2025"
    path.write_text("nc:\n  votes: {bare_key_session: '2027'}\n")
    import os

    os.utime(path, (1, 1))
    assert manifest_value("nc", "votes.bare_key_session", "2025") == "2027"


def _reload_profiles():
    return importlib.reload(resilience_profiles).RESILIENCE_PROFILES


def test_profiles_carry_todays_values_with_and_without_the_manifest(manifest, tmp_path, monkeypatch):
    manifest(MANIFEST)
    with_manifest = _reload_profiles()
    monkeypatch.setenv("DDP_JURISDICTIONS_MANIFEST", str(tmp_path / "absent.yaml"))
    without = _reload_profiles()
    for state in ("mi", "fl"):
        for p in (with_manifest[state], without[state]):
            assert p.requests_per_minute == 10
            assert p.circuit_breaker_max_consecutive_blocks == 3


def test_profiles_follow_the_manifest(manifest):
    manifest(
        "mi:\n  waf: {requests_per_minute: 4, max_consecutive_blocks: 5}\n"
        "fl:\n  waf: {requests_per_minute: 6, max_consecutive_blocks: 2}\n"
    )
    profiles = _reload_profiles()
    assert (profiles["mi"].requests_per_minute, profiles["mi"].circuit_breaker_max_consecutive_blocks) == (4, 5)
    assert (profiles["fl"].requests_per_minute, profiles["fl"].circuit_breaker_max_consecutive_blocks) == (6, 2)


def test_mi_env_var_still_wins_over_the_manifest(manifest, monkeypatch):
    manifest("mi:\n  waf: {requests_per_minute: 4}\n")
    monkeypatch.setenv("MI_SCRAPELIB_RPM", "5")
    assert _reload_profiles()["mi"].requests_per_minute == 5


@pytest.fixture(autouse=True)
def _restore_profiles(monkeypatch):
    yield
    monkeypatch.undo()
    ddp_manifest._cache.clear()
    importlib.reload(resilience_profiles)
