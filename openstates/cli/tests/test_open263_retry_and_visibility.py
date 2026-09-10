"""OPEN-263: a failed S3 upload must be retryable, and a failed local persist must be visible.

Found live 2026-09-09/10 during OPEN-192's Fargate archive validation. Two related bugs, same
root design gap (nothing above the raw log line distinguished "attempted and succeeded" from
"attempted and failed"):

1. The skip-check only asked whether a `BillVersionDocument` row exists for a document's
   natural key -- never whether `archive_location` was actually set. A document whose
   extraction succeeded but whose S3 upload failed got a row with `archive_location=None`,
   and every future run's skip-check saw "a row exists" and never retried it -- a permanent
   dead end, confirmed against a real production row (Arizona's SCM1004, stuck since a
   2026-09-09 IAM-blocked run).
2. A local-persist `OSError` (found live: a container permission bug) incremented no counter
   at all -- not `fetched` (already incremented earlier), not `fetch_errors`, nothing -- so a
   systemic write failure was indistinguishable from "nothing new to archive" in the summary
   line `run-archive.sh`/`cloud_archiver.py` actually reports.

Uses the same real-Postgres, real-function testing style `test_archive_concurrent_writes.py`
already established for this same function -- the defects live in exactly the kind of
DB-state-across-calls interaction a mocked unit would not exercise faithfully.
"""

from unittest import mock

import pytest

from openstates.cli.text_extract import archive_bill_versions
from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_text_extract import _make_bill


URL = "https://x.test/v1.pdf"
TEXT = "SECTION 1\nOriginal text."


def _patches(*, upload_result=None, persist_raises=False):
    patches = [
        mock.patch(
            "openstates.cli.text_extract._fetch_bytes",
            return_value=TEXT.encode("utf-8"),
        ),
        mock.patch(
            "openstates.cli.text_extract.get_extract_func",
            return_value=(lambda data, meta: data.decode("utf-8")),
        ),
        mock.patch(
            "openstates.cli.text_extract._upload_and_verify", return_value=upload_result
        ),
        mock.patch("openstates.cli.text_extract._block_page_reason", return_value=None),
        mock.patch("os.makedirs"),
    ]
    if persist_raises:
        patches.append(
            mock.patch(
                "builtins.open", side_effect=OSError(13, "Permission denied", "/app/_archive")
            )
        )
    else:
        patches.append(mock.patch("builtins.open", mock.mock_open()))
    return patches


def _run_with(patches, fn):
    for cm in patches:
        cm.start()
    try:
        return fn()
    finally:
        for cm in reversed(patches):
            cm.stop()


def _one_link_bill():
    bill = _make_bill()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=URL, media_type="application/pdf")
    return bill


@pytest.mark.django_db
class TestFailedUploadIsRetryable:
    def test_a_row_with_no_archive_location_is_not_skipped_on_the_next_run(self):
        """The bug, reproduced directly: a first run whose upload fails leaves a row with
        archive_location=None; a second run over the same bill must not treat that as done."""
        bill = _one_link_bill()

        first = _run_with(_patches(upload_result=None), lambda: archive_bill_versions(bill))
        assert first["archived"] == 1
        assert first["skipped"] == 0
        stuck = BillVersionDocument.objects.get(bill=bill, source_url=URL)
        assert stuck.archive_location is None
        assert stuck.is_error is False

        second = _run_with(_patches(upload_result=None), lambda: archive_bill_versions(bill))
        assert second["skipped"] == 0, "a failed-upload row must be retried, not skipped"
        assert second["archived"] == 1

    def test_a_retry_that_succeeds_actually_records_the_new_archive_location(self):
        """Not just "retried" -- the retry's own real result must stick, replacing the stale
        row rather than leaving it stuck forever even after a successful second attempt."""
        bill = _one_link_bill()

        _run_with(_patches(upload_result=None), lambda: archive_bill_versions(bill))
        assert BillVersionDocument.objects.get(bill=bill, source_url=URL).archive_location is None

        _run_with(
            _patches(upload_result="s3://ddp-bill-archive/real/path"),
            lambda: archive_bill_versions(bill),
        )
        recovered = BillVersionDocument.objects.get(bill=bill, source_url=URL)
        assert recovered.archive_location == "s3://ddp-bill-archive/real/path"
        # Exactly one row -- the retry replaced the stuck one, it didn't duplicate it.
        assert BillVersionDocument.objects.filter(bill=bill, source_url=URL).count() == 1

    def test_a_successfully_archived_row_is_still_skipped_normally(self):
        """The non-regression case: this fix must not make every already-archived document
        get re-fetched on every run -- only ones that never actually got an archive_location."""
        bill = _one_link_bill()

        _run_with(
            _patches(upload_result="s3://ddp-bill-archive/real/path"),
            lambda: archive_bill_versions(bill),
        )

        with mock.patch("openstates.cli.text_extract._fetch_bytes") as fetch:
            second = archive_bill_versions(bill)
            fetch.assert_not_called()
        assert second["skipped"] == 1
        assert second["archived"] == 0

    def test_an_is_error_row_is_still_skipped_not_retried(self):
        """is_error=True is a confirmed extraction failure -- OPEN-33/OPEN-229's own
        reprocess-in-place mechanism owns retrying those deliberately, not this skip-check.
        Retrying every is_error row automatically here would repeat the exact live-traffic
        cost that mechanism exists to avoid."""
        bill = _one_link_bill()
        BillVersionDocument.objects.create(
            bill=bill,
            version_note="Introduced",
            version_date="",
            source_url=URL,
            media_type="application/pdf",
            raw_text="",
            is_error=True,
            archive_location=None,
        )

        with mock.patch("openstates.cli.text_extract._fetch_bytes") as fetch:
            counters = archive_bill_versions(bill)
            fetch.assert_not_called()
        assert counters["skipped"] == 1
        assert counters["archived"] == 0


@pytest.mark.django_db
class TestPersistFailureIsVisible:
    def test_a_local_persist_failure_increments_its_own_counter(self):
        bill = _one_link_bill()

        counters = _run_with(
            _patches(persist_raises=True), lambda: archive_bill_versions(bill)
        )

        assert counters["persist_errors"] == 1
        assert counters["fetched"] == 1  # still counted -- the fetch itself succeeded
        assert counters["archived"] == 0
        assert BillVersionDocument.objects.filter(bill=bill).count() == 0

    def test_a_persist_failure_does_not_also_get_counted_as_a_fetch_error(self):
        """The two failure modes are genuinely different points in the pipeline -- a fetch_error
        never got the bytes at all; a persist_error fetched them and failed to stage them
        locally. Conflating them would make the summary line's own diagnosis less precise."""
        bill = _one_link_bill()

        counters = _run_with(
            _patches(persist_raises=True), lambda: archive_bill_versions(bill)
        )

        assert counters["fetch_errors"] == 0
        assert counters["persist_errors"] == 1

    def test_a_persist_failure_leaves_no_row_so_the_next_run_retries_it_naturally(self):
        """Unlike a failed S3 upload (which creates a row with archive_location=None and
        needed the skip-check fix above), a persist failure never reaches the create() call
        at all -- so it was already naturally retryable. Confirming that stays true."""
        bill = _one_link_bill()

        _run_with(_patches(persist_raises=True), lambda: archive_bill_versions(bill))
        assert BillVersionDocument.objects.filter(bill=bill).count() == 0

        second = _run_with(
            _patches(upload_result="s3://ddp-bill-archive/real/path"),
            lambda: archive_bill_versions(bill),
        )
        assert second["archived"] == 1
        assert second["skipped"] == 0


@pytest.mark.django_db
class TestArchiveCommandSummaryLine:
    """The user-visible incident was a clean-looking exit code masking real failures -- assert
    the CLI command's own status color and printed line, not just the counters dict."""

    @staticmethod
    def _run():
        from openstates.cli.text_extract import archive

        return archive.callback(state="ak")

    def test_persist_errors_turn_the_summary_yellow_not_green(self, capsys):
        _one_link_bill()

        _run_with(_patches(persist_raises=True), self._run)

        out = capsys.readouterr().out
        assert "persist_errors=1" in out

    def test_persist_errors_do_not_fail_the_run(self):
        """Yellow, not red -- a persist failure is recoverable (the doc gets retried next
        run), not a uniqueness violation worth sys.exit(1) over."""
        _one_link_bill()

        # no SystemExit
        _run_with(_patches(persist_raises=True), self._run)
