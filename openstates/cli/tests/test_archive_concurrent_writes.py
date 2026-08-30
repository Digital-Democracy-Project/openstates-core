"""OPEN-107: losing a race to another archiver must not fail the archive run.

`run-archive.sh`'s own header states that running an archive concurrently with a scrape
for the SAME jurisdiction is safe, "the natural-key skip check in os-text-extract makes an
already-archived version a cheap DB check, not a re-fetch". That was not true: two
archivers can both pass the skip check for the same link before either inserts, and the
loser's `IntegrityError` was counted in `conflicts` -- which `archive()` turns into
`sys.exit(1)`, failing the whole run.

Confirmed as this ticket's cause: the WA and VA full archives of 2026-07-28 ran
simultaneously (both started 12:28:52) and produced 347 and 1,820 of these respectively.
Every affected document is archived today, which a genuinely broken dedup key could not
produce -- 37,177 of 37,179 WA+VA links are present, and the 2 exceptions are Virginia
HB 30 fetch failures, not conflicts.

**Every race here is simulated by causing a REAL Postgres duplicate-key violation**, never
by raising `IntegrityError` by hand. /pm-review asked for that and it mattered a great
deal: a hand-raised error validates the classification but hides the fact that a genuine
failed INSERT marks the connection as needing rollback, so the recovery SELECT raises
`TransactionManagementError` instead of running. That is a real bug the mocked version
could not see, and it is why the insert now has its own savepoint. A hand-raised mock is
also simply the wrong model -- a competing writer commits on a *different* connection,
outside this run's savepoint, which is what these fixtures reproduce.
"""

from unittest import mock

import pytest
from django.db.utils import IntegrityError

from openstates.cli.text_extract import archive_bill_versions
from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_text_extract import _make_bill


V1_URL = "https://x.test/v1.pdf"
V2_URL = "https://x.test/v2.pdf"
V1_TEXT = "SECTION 1\nOriginal text."
V2_TEXT = "SECTION 1\nAmended text."
_TEXTS = {V1_URL: V1_TEXT, V2_URL: V2_TEXT}


def _base_patches():
    return [
        mock.patch(
            "openstates.cli.text_extract._fetch_bytes",
            side_effect=lambda url: _TEXTS[url].encode("utf-8"),
        ),
        mock.patch(
            "openstates.cli.text_extract.get_extract_func",
            side_effect=lambda md: (lambda data, meta: data.decode("utf-8")),
        ),
        mock.patch("openstates.cli.text_extract._upload_and_verify", return_value=None),
        mock.patch("openstates.cli.text_extract._block_page_reason", return_value=None),
        mock.patch("os.makedirs"),
        mock.patch("builtins.open", mock.mock_open()),
    ]


def _run_with(patches, fn):
    for cm in patches:
        cm.start()
    try:
        return fn()
    finally:
        for cm in reversed(patches):
            cm.stop()


def _blind_only_the_skip_check():
    """Make the FIRST `objects.filter()` call per link -- the already-archived skip check --
    report nothing, and let every later call, including the recovery lookup, behave
    normally. That is exactly how a lost race looks from inside the losing run: the row was
    not visible when it checked, and is by the time it inserts.

    Two filter calls happen per link (skip check, then recovery), so a call counter
    suffices. Deliberately narrow -- blinding the recovery lookup would defeat the point.
    """
    real_filter = BillVersionDocument.objects.filter
    state = {"calls": 0}

    class _Empty:
        def first(self):
            return None

    def blinded(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return _Empty()
        return real_filter(*args, **kwargs)

    return mock.patch.object(BillVersionDocument.objects, "filter", side_effect=blinded)


def _winning_row(bill, text=V1_TEXT, is_error=False):
    """The row the other archiver committed before ours."""
    return BillVersionDocument.objects.create(
        bill=bill,
        version_note="Introduced",
        version_date="",
        source_url=V1_URL,
        media_type="application/pdf",
        raw_text=text,
        is_error=is_error,
    )


def _two_version_bill():
    bill = _make_bill()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=V1_URL, media_type="application/pdf")
    v2 = bill.versions.create(note="Enrolled", date="")
    v2.links.create(url=V2_URL, media_type="application/pdf")
    return bill


@pytest.mark.django_db
class TestARealDuplicateKeyViolation:
    def test_a_lost_race_is_counted_separately_from_a_real_conflict(self):
        """The fix. Losing the race leaves the document archived, so it must not land in
        `conflicts` -- the counter `archive()` exits 1 on."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url=V1_URL, media_type="application/pdf")
        _winning_row(bill)

        counters = _run_with(
            _base_patches() + [_blind_only_the_skip_check()],
            lambda: archive_bill_versions(bill),
        )

        assert counters["concurrent_writes"] == 1
        assert counters["conflicts"] == 0
        # exactly one row -- the winner's, not a duplicate
        assert BillVersionDocument.objects.filter(bill=bill).count() == 1

    def test_the_connection_survives_and_the_run_continues(self):
        """The property a hand-raised IntegrityError cannot show: after a genuine failed
        INSERT the recovery SELECT works, AND the run goes on to insert later documents.
        Without the savepoint around the insert this raises TransactionManagementError."""
        bill = _two_version_bill()
        _winning_row(bill)

        counters = _run_with(
            _base_patches() + [_blind_only_the_skip_check()],
            lambda: archive_bill_versions(bill),
        )

        assert counters["concurrent_writes"] == 1
        assert counters["archived"] == 1  # Enrolled still got written

    def test_a_lost_race_does_not_break_the_next_versions_diff(self):
        """The quieter half of the bug. The losing run used to drop that media type from
        the version's baseline entirely, so the NEXT version's document of the same
        rendering got a diff against an older version -- or none at all -- purely because
        of a lost race. The baseline is now taken from the row the other writer stored."""
        bill = _two_version_bill()
        _winning_row(bill)

        _run_with(
            _base_patches() + [_blind_only_the_skip_check()],
            lambda: archive_bill_versions(bill),
        )

        enrolled = BillVersionDocument.objects.get(
            bill=bill, version_note="Enrolled", media_type="application/pdf"
        )
        diff = enrolled.diff_from_previous_version
        assert diff is not None, "lost race silently dropped the diff baseline"
        assert "-Original text." in diff
        assert "+Amended text." in diff

    def test_an_errored_row_written_by_the_other_run_does_not_become_a_baseline(self):
        """Eligibility rules apply to a row recovered this way exactly as they do to one
        this run fetched itself -- an error row has no usable text."""
        bill = _two_version_bill()
        _winning_row(bill, text="", is_error=True)

        counters = _run_with(
            _base_patches() + [_blind_only_the_skip_check()],
            lambda: archive_bill_versions(bill),
        )

        assert counters["concurrent_writes"] == 1
        enrolled = BillVersionDocument.objects.get(
            bill=bill, version_note="Enrolled", media_type="application/pdf"
        )
        assert enrolled.diff_from_previous_version is None

    def test_an_integrity_error_with_no_stored_row_is_still_a_real_conflict(self):
        """The alarm must survive. An IntegrityError leaving no row for the natural key is a
        genuine uniqueness violation and keeps its run-failing treatment. Hand-raised here
        deliberately: the point is that no row exists, so there is nothing a faithful
        fixture could have committed."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url=V1_URL, media_type="application/pdf")

        def always_raise(**kwargs):
            raise IntegrityError("something genuinely wrong")

        counters = _run_with(
            _base_patches()
            + [
                mock.patch.object(
                    BillVersionDocument.objects, "create", side_effect=always_raise
                )
            ],
            lambda: archive_bill_versions(bill),
        )

        assert counters["conflicts"] == 1
        assert counters["concurrent_writes"] == 0


@pytest.mark.django_db
class TestArchiveCommandExitCode:
    """The user-visible incident was a non-zero archive run, so assert that directly rather
    than only at the counter level."""

    @staticmethod
    def _run():
        from openstates.cli.text_extract import archive

        return archive.callback(state="ak")

    def test_a_concurrent_write_does_not_fail_the_run(self):
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url=V1_URL, media_type="application/pdf")
        _winning_row(bill)

        # no SystemExit -- this is the whole point of the ticket
        _run_with(_base_patches() + [_blind_only_the_skip_check()], self._run)

    def test_a_genuine_conflict_still_fails_the_run(self):
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url=V1_URL, media_type="application/pdf")

        def always_raise(**kwargs):
            raise IntegrityError("something genuinely wrong")

        def _expect_exit():
            with pytest.raises(SystemExit) as exc:
                self._run()
            assert exc.value.code == 1

        _run_with(
            _base_patches()
            + [
                mock.patch.object(
                    BillVersionDocument.objects, "create", side_effect=always_raise
                )
            ],
            _expect_exit,
        )
