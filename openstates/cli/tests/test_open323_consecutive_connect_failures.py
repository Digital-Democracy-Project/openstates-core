"""OPEN-323: a run whose every fetch is failing to connect must stop, not crawl for 12 hours.

Found 2026-10-04: MA's archive made ~1 bill per 16 minutes for 11 hours once malegislature.gov
stopped accepting connections (each document: scrapelib's 5 retries, then a ConnectTimeout that
was absorbed as one more `fetch_errors`), until ECS's 12-hour wait ran out. `archive()` already
turns a ScrapeError into exit 1 for OPEN-52's WAF breaker (covered in test_text_extract.py), so
these tests only pin the new counting: what trips it, what resets it, what must never count.
"""

from unittest import mock

import pytest
import requests
import scrapelib

from openstates.cli import text_extract
from openstates.cli.text_extract import archive_bill_versions
from openstates.exceptions import ScrapeError

from openstates.cli.tests.test_text_extract import _make_bill

LIMIT = text_extract._MAX_CONSECUTIVE_CONNECT_FAILURES
TEXT = b"SECTION 1\nSome text."


@pytest.fixture(autouse=True)
def _reset_counter():
    text_extract._consecutive_connect_failures = 0
    yield
    text_extract._consecutive_connect_failures = 0


def _bill_with_links(n):
    bill = _make_bill()
    version = bill.versions.create(note="Introduced", date="")
    for i in range(n):
        version.links.create(url=f"https://x.test/doc{i}.pdf", media_type="application/pdf")
    return bill


def _run(bill, fetch_side_effect):
    """Runs the real archive_bill_versions() with only the network and storage stubbed."""
    with mock.patch(
        "openstates.cli.text_extract._fetch_bytes", side_effect=fetch_side_effect
    ) as fetch, mock.patch(
        "openstates.cli.text_extract.get_extract_func",
        return_value=(lambda data, meta: data.decode("utf-8")),
    ), mock.patch(
        "openstates.cli.text_extract._upload_and_verify", return_value=None
    ), mock.patch(
        "openstates.cli.text_extract._block_page_reason", return_value=None
    ), mock.patch("os.makedirs"), mock.patch("builtins.open", mock.mock_open()):
        try:
            return archive_bill_versions(bill), fetch
        except ScrapeError as e:
            return e, fetch


@pytest.mark.django_db
class TestConsecutiveConnectFailures:
    def test_a_run_of_connect_timeouts_aborts_at_the_limit_without_trying_the_rest(self):
        bill = _bill_with_links(LIMIT + 3)

        outcome, fetch = _run(bill, requests.exceptions.ConnectTimeout("timed out"))

        assert isinstance(outcome, ScrapeError)
        assert "consecutive connection failures" in str(outcome)
        assert fetch.call_count == LIMIT, "must stop at the limit, not keep crawling"

    def test_a_success_in_the_middle_resets_the_count(self):
        """LIMIT-1 failures, one success, LIMIT-1 more failures: never LIMIT in a row."""
        bill = _bill_with_links(2 * (LIMIT - 1) + 1)
        timeout = requests.exceptions.ConnectTimeout("timed out")
        effects = [timeout] * (LIMIT - 1) + [TEXT] + [timeout] * (LIMIT - 1)

        outcome, fetch = _run(bill, effects)

        assert not isinstance(outcome, Exception)
        assert outcome["fetch_errors"] == 2 * (LIMIT - 1)
        assert outcome["fetched"] == 1
        assert fetch.call_count == len(effects)

    def test_plain_timeouts_and_connection_errors_both_count(self):
        bill = _bill_with_links(LIMIT)
        effects = [
            requests.exceptions.ReadTimeout("slow"),
            requests.exceptions.ConnectionError("refused"),
        ] * LIMIT

        outcome, _ = _run(bill, effects)

        assert isinstance(outcome, ScrapeError)

    def test_http_errors_never_count_toward_the_limit(self):
        """A dead link (404) is routine and says nothing about the site -- MI logs plenty."""
        bill = _bill_with_links(LIMIT + 3)

        outcome, fetch = _run(bill, scrapelib.HTTPError(mock.Mock(status_code=404)))

        assert not isinstance(outcome, Exception)
        assert outcome["fetch_errors"] == LIMIT + 3
        assert fetch.call_count == LIMIT + 3

    def test_an_http_error_between_timeouts_resets_the_count(self):
        """A 404 means the site answered; four timeouts, a 404, then four more is not a run of
        LIMIT connection failures."""
        bill = _bill_with_links(2 * (LIMIT - 1) + 1)
        timeout = requests.exceptions.ConnectTimeout("timed out")
        dead_link = scrapelib.HTTPError(mock.Mock(status_code=404))
        effects = [timeout] * (LIMIT - 1) + [dead_link] + [timeout] * (LIMIT - 1)

        outcome, _ = _run(bill, effects)

        assert not isinstance(outcome, Exception)
        assert outcome["fetch_errors"] == len(effects)

    def test_the_count_carries_across_bills_in_one_run(self):
        """The run is the unit, not the bill: one failing document each across several bills
        still adds up (MA's crawl was spread across thousands of bills)."""
        timeout = requests.exceptions.ConnectTimeout("timed out")
        raised = None
        for _ in range(LIMIT):
            outcome, _fetch = _run(_bill_with_links(1), timeout)
            if isinstance(outcome, ScrapeError):
                raised = outcome

        assert raised is not None
