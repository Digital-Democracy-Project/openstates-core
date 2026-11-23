"""OPEN-800: certificates are checked on every request path; an exception covers only its own host.

No network: a fake transport adapter mounted on each scraper records the `verify` value requests
hands to the connection for every hop, which is exactly what urllib3 would use for the handshake.
"""

import ast
import pathlib
from types import SimpleNamespace

import pytest
import requests

from openstates import settings
from openstates.cli import text_extract
from openstates.scrape import State
from openstates.scrape.base import Scraper
from openstates.utils import tls
from openstates.utils.tls import TlsException, VerifyingScraper, verify_for


class RecordingAdapter(requests.adapters.BaseAdapter):
    """Answers every request itself; `redirects` maps a URL to the URL it 302s to."""

    def __init__(self, redirects=None):
        super().__init__()
        self.seen = []  # (url, verify) per hop
        self.redirects = redirects or {}

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.seen.append((request.url, verify))
        resp = requests.Response()
        resp.request = request
        resp.url = request.url
        resp.raw = None
        resp._content = b"ok"
        resp._content_consumed = True
        if request.url in self.redirects:
            resp.status_code = 302
            resp.headers["Location"] = self.redirects[request.url]
        else:
            resp.status_code = 200
        return resp

    def close(self):
        pass


def _wire(scraper, monkeypatch, redirects=None):
    """Mount the recorder; monkeypatch undoes it, so the shared text_extract.scraper is left as found."""
    monkeypatch.setattr(scraper, "adapters", scraper.adapters.copy())
    monkeypatch.setattr(scraper, "requests_per_minute", 0)  # no throttle sleep
    monkeypatch.setattr(scraper, "retry_attempts", 0)
    adapter = RecordingAdapter(redirects)
    scraper.mount("https://", adapter)
    scraper.mount("http://", adapter)
    return adapter


class NewJersey(State):
    pass


@pytest.fixture
def one_exception(monkeypatch):
    monkeypatch.setitem(
        tls.TLS_VERIFY_EXCEPTIONS,
        "bad.example.gov",
        TlsException(reason="test", date="2026-11-23", ticket="OPEN-800"),
    )


# --- the rule -----------------------------------------------------------------------------------


def test_default_is_checked_and_caller_false_does_not_turn_it_off():
    assert verify_for("https://le.utah.gov/x") is True
    assert verify_for("https://le.utah.gov/x", None) is True
    assert verify_for("https://le.utah.gov/x", False) is True
    assert verify_for("https://le.utah.gov/x", True) is True


def test_caller_ca_bundle_path_is_kept_because_it_still_verifies():
    assert verify_for("https://le.utah.gov/x", "/etc/ssl/bundle.pem") == "/etc/ssl/bundle.pem"


def test_exception_applies_to_its_own_host_only(one_exception):
    assert verify_for("https://bad.example.gov/doc.pdf") is False
    assert verify_for("https://BAD.example.gov:8443/doc.pdf", True) is False
    # not a parent, a sibling, a sub-host, a look-alike, or a host that merely contains it
    for url in (
        "https://example.gov/",
        "https://good.example.gov/",
        "https://www.bad.example.gov/",
        "https://bad.example.gov.evil.net/",
        "https://notbad.example.gov/",
        "https://evil.net/?u=https://bad.example.gov/",
    ):
        assert verify_for(url, False) is True, url


def test_shipped_exceptions_are_named_dated_and_ticketed():
    for host, exc in tls.TLS_VERIFY_EXCEPTIONS.items():
        assert host == host.lower() and "/" not in host and ":" not in host
        assert exc.reason and exc.ticket
        assert len(exc.date) == 10 and exc.date[4] == "-" and exc.date[7] == "-"


def test_no_global_switch_left_in_settings():
    assert not hasattr(settings, "SCRAPELIB_VERIFY")


# --- every request path ---------------------------------------------------------------------


def _scrapers_under_test():
    profile = SimpleNamespace(name="open800-test-profile", requests_per_minute=0)
    try:
        profiled = text_extract._scraper_for_profile(profile)
    finally:
        text_extract._profile_scrapers.pop(profile.name, None)
    return {
        "scrape base Scraper": Scraper(NewJersey(), "/tmp/"),
        "text_extract plain scraper": text_extract.scraper,
        "text_extract WAF-profile scraper": profiled,
    }


@pytest.mark.parametrize("name", list(_scrapers_under_test()))
def test_every_scraper_checks_certificates_even_when_told_not_to(name, monkeypatch):
    scraper = _scrapers_under_test()[name]
    adapter = _wire(scraper, monkeypatch)
    monkeypatch.setattr(scraper, "verify", False)  # a scraper class's `verify = False` (MA, VA, OK ...)
    scraper.get("https://le.utah.gov/a")
    scraper.get("https://le.utah.gov/b", verify=False)
    scraper.post("https://le.utah.gov/c", data={"x": 1}, verify=False)
    assert [v for _, v in adapter.seen] == [True, True, True], adapter.seen


@pytest.mark.parametrize("name", list(_scrapers_under_test()))
def test_every_scraper_honours_an_exception_for_its_host_only(name, monkeypatch, one_exception):
    scraper = _scrapers_under_test()[name]
    adapter = _wire(scraper, monkeypatch)
    scraper.get("https://bad.example.gov/doc.pdf")
    scraper.get("https://good.example.gov/doc.pdf", verify=False)
    assert adapter.seen == [
        ("https://bad.example.gov/doc.pdf", False),
        ("https://good.example.gov/doc.pdf", True),
    ]


def test_redirect_is_judged_by_the_host_it_lands_on(monkeypatch, one_exception):
    scraper = VerifyingScraper()
    adapter = _wire(
        scraper,
        monkeypatch,
        redirects={
            "https://bad.example.gov/start": "https://good.example.gov/end",
            "https://good.example.gov/start": "https://bad.example.gov/end",
        },
    )
    scraper.get("https://bad.example.gov/start")
    scraper.get("https://good.example.gov/start")
    assert adapter.seen == [
        ("https://bad.example.gov/start", False),
        ("https://good.example.gov/end", True),
        ("https://good.example.gov/start", True),
        ("https://bad.example.gov/end", False),
    ]


def test_no_code_in_openstates_turns_verification_off():
    """Static guard: outside tests, nothing passes verify=False, sets .verify = False, or builds a
    plain scrapelib.Scraper (which would skip VerifyingScraper's per-host rule)."""
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "verify":
                if isinstance(node.value, ast.Constant) and node.value.value is False:
                    offenders.append(f"{path}:{node.value.lineno} verify=False")
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if (
                        isinstance(t, ast.Attribute)
                        and t.attr == "verify"
                        and isinstance(node.value, ast.Constant)
                        and node.value.value is False
                    ):
                        offenders.append(f"{path}:{node.lineno} .verify = False")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Scraper"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "scrapelib"
            ):
                offenders.append(f"{path}:{node.lineno} scrapelib.Scraper(...)")
            if isinstance(node, ast.Constant) and node.value == "VERIFY_CERTS":
                offenders.append(f"{path}:{node.lineno} reads VERIFY_CERTS")
    assert offenders == []
