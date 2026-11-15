"""OPEN-506: Massachusetts' archive remembers the bill PDFs malegislature.gov answers 404 for.

Every Massachusetts archive asked for the same ~20 PDFs that do not exist. Most weeks the site
answered 404 at once; on 10-16 and 11-08 it did not answer at all for them, five no-answers in a
row tripped OPEN-323's guard on the run's first five fetches, and the run saved nothing. These
tests use those real addresses, with the network stubbed (nothing here reaches the site).
"""

import datetime
from unittest import mock

import pytest
import requests
import scrapelib
from django.utils import timezone

from openstates.cli import text_extract
from openstates.cli.tests.test_open323_consecutive_connect_failures import (
    LIMIT,
    TEXT,
    _run,
)
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillDocumentNotFound
from openstates.exceptions import ScrapeError

# The first five never-existing files, in the order both failed runs asked for them.
MISSING = [
    "https://malegislature.gov/Bills/194/SD3039.pdf",
    "https://malegislature.gov/Bills/194/HD2304.pdf",
    "https://malegislature.gov/Bills/194/SD3964.pdf",
    "https://malegislature.gov/Bills/194/HD172.pdf",
    "https://malegislature.gov/Bills/194/HD178.pdf",
]
# Files that do exist (a new week's texts).
NEW = [f"https://malegislature.gov/Bills/194/H{4000 + i}.pdf" for i in range(LIMIT)]
TIMEOUT = requests.exceptions.ConnectTimeout(
    "HTTPSConnectionPool(host='malegislature.gov')"
)


def _not_found(url):
    return scrapelib.HTTPError(mock.Mock(status_code=404, url=url, text=""))


@pytest.fixture(autouse=True)
def _reset_run_state():
    text_extract._consecutive_connect_failures = 0
    text_extract._not_found_recheck_unanswered = False
    yield
    text_extract._consecutive_connect_failures = 0
    text_extract._not_found_recheck_unanswered = False


def _ma_bill(
    urls, jid="ocd-jurisdiction/country:us/state:ma/government", name="Massachusetts"
):
    bill = _make_bill(jid=jid, jurisdiction_name=name)
    version = bill.versions.create(note="Introduced", date="")
    for url in urls:
        version.links.create(url=url, media_type="application/pdf")
    return bill


def _site(missing_answer):
    """The stubbed site: every MISSING file gets `missing_answer` (a 404 or a no-answer), every
    other file exists."""

    def fetch(url):
        if url in MISSING:
            raise missing_answer(url) if callable(missing_answer) else missing_answer
        return TEXT

    return fetch


def _remember(urls, days_ago):
    when = timezone.now() - datetime.timedelta(days=days_ago)
    for url in urls:
        BillDocumentNotFound.objects.create(
            source_url=url, first_not_found_at=when, last_checked_at=when
        )


def _fetched_urls(fetch):
    return [c.args[0] for c in fetch.call_args_list]


@pytest.mark.django_db
class TestRememberedNotFound:
    def test_a_404_is_remembered(self):
        outcome, _ = _run(_ma_bill(MISSING), _site(_not_found))

        assert outcome["fetch_errors"] == len(MISSING)
        assert set(
            BillDocumentNotFound.objects.values_list("source_url", flat=True)
        ) == set(MISSING)

    def test_the_bad_day_saves_the_new_texts_instead_of_stopping(self):
        """11-08 replayed after a good week: the five no-answer files are not asked for, so the
        run reaches the new texts and saves them."""
        bill = _ma_bill(MISSING)
        _run(bill, _site(_not_found))  # a good week: 404s, remembered

        bill.versions.get().links.create(url=NEW[0], media_type="application/pdf")
        outcome, fetch = _run(bill, _site(TIMEOUT))  # the bad day

        assert not isinstance(outcome, Exception)
        assert _fetched_urls(fetch) == NEW[:1]
        assert outcome["fetched"] == 1
        assert outcome["archived"] == 1
        assert outcome["skipped"] == len(MISSING)

    def test_the_guard_still_stops_when_files_that_exist_keep_failing(self):
        """OPEN-323 stays intact: the site really is unreachable when files never answered 404
        keep failing, remembered files or not."""
        _remember(MISSING, days_ago=1)
        bill = _ma_bill(MISSING + NEW)

        outcome, fetch = _run(bill, TIMEOUT)

        assert isinstance(outcome, ScrapeError)
        assert "consecutive connection failures" in str(outcome)
        assert sorted(_fetched_urls(fetch)) == sorted(NEW)

    def test_without_a_memory_the_bad_day_still_stops_the_run(self):
        """The 11-08 failure, for contrast: the same five no-answers with nothing remembered."""
        outcome, fetch = _run(_ma_bill(MISSING), _site(TIMEOUT))

        assert isinstance(outcome, ScrapeError)
        assert fetch.call_count == LIMIT

    def test_a_due_recheck_with_no_answer_does_not_count_and_stops_other_rechecks(self):
        _remember(MISSING, days_ago=29)
        bill = _ma_bill(MISSING + NEW[:1])

        outcome, fetch = _run(bill, _site(TIMEOUT))

        assert not isinstance(outcome, Exception)
        asked = _fetched_urls(fetch)
        assert (
            len([u for u in asked if u in MISSING]) == 1
        ), "one re-check, then stop re-checking"
        assert NEW[0] in asked and outcome["archived"] == 1
        assert text_extract._consecutive_connect_failures == 0
        (rechecked_url,) = [u for u in asked if u in MISSING]
        rechecked = BillDocumentNotFound.objects.get(source_url=rechecked_url)
        assert timezone.now() - rechecked.last_checked_at < datetime.timedelta(
            minutes=1
        )
        assert BillDocumentNotFound.objects.count() == len(MISSING)

    def test_a_recheck_no_answer_neither_counts_nor_resets_the_count(self):
        """LIMIT-1 new files fail, then a re-check gets no answer: still LIMIT-1, so one more new
        failure reaches the stop (a re-check must not hide a run of real failures either)."""
        text_extract._consecutive_connect_failures = LIMIT - 1
        _remember(MISSING[:1], days_ago=29)
        later = "https://malegislature.gov/Bills/194/SD4000.pdf"  # sorts after SD3039
        bill = _ma_bill(MISSING[:1] + [later])

        outcome, fetch = _run(bill, TIMEOUT)

        assert isinstance(outcome, ScrapeError)
        assert _fetched_urls(fetch) == [MISSING[0], later]

    def test_a_due_recheck_that_404s_again_restarts_the_wait(self):
        _remember(MISSING[:1], days_ago=29)
        first = BillDocumentNotFound.objects.get().first_not_found_at

        _run(_ma_bill(MISSING[:1]), _site(_not_found))

        row = BillDocumentNotFound.objects.get()
        assert row.first_not_found_at == first
        assert timezone.now() - row.last_checked_at < datetime.timedelta(minutes=1)

    def test_a_due_recheck_that_succeeds_archives_it_and_forgets_it(self):
        _remember(MISSING[:1], days_ago=29)

        outcome, _ = _run(_ma_bill(MISSING[:1]), lambda url: TEXT)

        assert outcome["archived"] == 1
        assert not BillDocumentNotFound.objects.exists()

    def test_other_states_are_unchanged(self):
        """Not opted in: a 404 is not remembered and is asked for again on every run."""
        bill = _ma_bill(
            MISSING, jid="ocd-jurisdiction/country:us/state:ak/government", name="Test"
        )
        for _ in range(2):
            outcome, fetch = _run(bill, _site(_not_found))
            assert fetch.call_count == len(MISSING)
            assert outcome["fetch_errors"] == len(MISSING)
        assert not BillDocumentNotFound.objects.exists()


@pytest.mark.django_db
def test_each_archive_run_starts_with_rechecks_allowed():
    from click.testing import CliRunner

    from openstates.cli.text_extract import archive

    bill = _make_bill(jid="ocd-jurisdiction/country:us/state:ma/government")
    text_extract._not_found_recheck_unanswered = True

    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid",
        return_value=bill.legislative_session.jurisdiction_id,
    ), mock.patch("openstates.cli.text_extract.archive_bill_versions", return_value={}):
        result = CliRunner().invoke(archive, ["ma"])

    assert result.exit_code == 0
    assert text_extract._not_found_recheck_unanswered is False
