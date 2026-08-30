"""OPEN-107: losing a race to another archiver must not fail the run.

`run-archive.sh`'s own header states that running an archive concurrently with a scrape
for the SAME jurisdiction is safe, "the natural-key skip check in os-text-extract makes an
already-archived version a cheap DB check, not a re-fetch". That was not true: two
archivers can both pass the skip check for the same link before either inserts, and the
loser's `IntegrityError` was counted as a conflict -- which `archive()` turns into
`sys.exit(1)`, failing the whole run.

Confirmed as this ticket's cause: the WA and VA full archives of 2026-07-28 ran
simultaneously (both started 12:28:52) and produced 347 and 1,820 of these respectively.
Every affected document is archived today, which a genuinely broken dedup key could not
produce -- 37,177 of 37,179 WA+VA links are present, and the 2 exceptions are Virginia
HB 30 fetch failures, not conflicts.
"""

from unittest import mock

import pytest
from django.db.utils import IntegrityError

from openstates.cli.text_extract import archive_bill_versions
from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_text_extract import _make_bill


V1_TEXT = "SECTION 1\nOriginal text."
V2_TEXT = "SECTION 1\nAmended text."


def _archive(bill, texts_by_url):
    def fake_fetch_bytes(url):
        return texts_by_url[url].encode("utf-8")

    def fake_extract_func(metadata):
        return lambda data, meta: data.decode("utf-8")

    stack = [
        mock.patch(
            "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch_bytes
        ),
        mock.patch(
            "openstates.cli.text_extract.get_extract_func",
            side_effect=fake_extract_func,
        ),
        mock.patch("openstates.cli.text_extract._upload_and_verify", return_value=None),
        mock.patch("openstates.cli.text_extract._block_page_reason", return_value=None),
        mock.patch("os.makedirs"),
        mock.patch("builtins.open", mock.mock_open()),
    ]
    for cm in stack:
        cm.start()
    try:
        return archive_bill_versions(bill)
    finally:
        for cm in reversed(stack):
            cm.stop()


@pytest.mark.django_db
class TestConcurrentWriteIsNotAConflict:
    def _simulate_lost_race(self, bill, version_note, url, media_type, text):
        """Stand in for another archiver that inserted this exact row between our skip
        check and our insert: make the real create raise IntegrityError, and put the row
        in the database as that other writer would have."""
        real_create = BillVersionDocument.objects.create

        def create_or_lose(**kwargs):
            if kwargs.get("source_url") == url:
                # the row the other archiver wrote, committed before ours
                real_create(
                    bill=kwargs["bill"],
                    version_note=kwargs["version_note"],
                    version_date=kwargs["version_date"],
                    source_url=url,
                    media_type=media_type,
                    raw_text=text,
                    is_error=False,
                )
                raise IntegrityError("duplicate key value violates unique constraint")
            return real_create(**kwargs)

        return mock.patch.object(
            BillVersionDocument.objects, "create", side_effect=create_or_lose
        )

    def test_a_lost_race_is_counted_separately_from_a_real_conflict(self):
        """The fix. Losing the race leaves the document archived, so it must not land in
        `conflicts` -- the counter `archive()` exits 1 on."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")

        with self._simulate_lost_race(
            bill, "Introduced", "https://x.test/v1.pdf", "application/pdf", V1_TEXT
        ):
            counters = _archive(bill, {"https://x.test/v1.pdf": V1_TEXT})

        assert counters["concurrent_writes"] == 1
        assert counters["conflicts"] == 0
        # and the document really is archived -- by the other writer
        assert BillVersionDocument.objects.filter(bill=bill).count() == 1

    def test_an_integrity_error_with_no_stored_row_is_still_a_real_conflict(self):
        """The alarm must survive. An IntegrityError that leaves no row for the natural key
        is a genuine uniqueness violation and keeps its run-failing treatment."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")

        def always_raise(**kwargs):
            raise IntegrityError("something genuinely wrong")

        with mock.patch.object(
            BillVersionDocument.objects, "create", side_effect=always_raise
        ):
            counters = _archive(bill, {"https://x.test/v1.pdf": V1_TEXT})

        assert counters["conflicts"] == 1
        assert counters["concurrent_writes"] == 0

    def test_a_lost_race_does_not_break_the_next_versions_diff(self):
        """The quieter half of the bug. The losing run used to drop that media type from
        the version's baseline entirely, so the NEXT version's document of the same
        rendering got a diff against an older version -- or none at all -- purely because
        of a lost race. The baseline is now taken from the row the other writer stored."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")

        with self._simulate_lost_race(
            bill, "Introduced", "https://x.test/v1.pdf", "application/pdf", V1_TEXT
        ):
            _archive(
                bill,
                {"https://x.test/v1.pdf": V1_TEXT, "https://x.test/v2.pdf": V2_TEXT},
            )

        # Deliberately asserts nothing about the new counter, so this is a pure
        # behavioural regression test that runs unchanged against main -- where it fails
        # because the losing run dropped the baseline and Enrolled got no diff at all.
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
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")

        real_create = BillVersionDocument.objects.create

        def create_or_lose(**kwargs):
            if kwargs.get("source_url") == "https://x.test/v1.pdf":
                real_create(
                    bill=kwargs["bill"],
                    version_note=kwargs["version_note"],
                    version_date=kwargs["version_date"],
                    source_url="https://x.test/v1.pdf",
                    media_type="application/pdf",
                    raw_text="",
                    is_error=True,
                )
                raise IntegrityError("duplicate key")
            return real_create(**kwargs)

        with mock.patch.object(
            BillVersionDocument.objects, "create", side_effect=create_or_lose
        ):
            counters = _archive(
                bill,
                {"https://x.test/v1.pdf": V1_TEXT, "https://x.test/v2.pdf": V2_TEXT},
            )

        assert counters["concurrent_writes"] == 1
        enrolled = BillVersionDocument.objects.get(
            bill=bill, version_note="Enrolled", media_type="application/pdf"
        )
        assert enrolled.diff_from_previous_version is None
