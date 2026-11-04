"""OPEN-487: a document whose archive file name would be too long must still be saved, and a
document the run could not save must be named in the log, not only counted.

Found live 2026-11-04 in Nebraska's archive trial: one amendment (AM2748) was fetched and then
dropped with "[Errno 36] File name too long"; the run exited 0 and the loss showed only as
persist_errors=1. The name is built from the version note, which is unbounded.
"""

import os
from unittest import mock

import pytest

from openstates import settings
from openstates.cli.text_extract import _archive_path, archive_bill_versions
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.cli.tests.test_open263_retry_and_visibility import (
    TEXT,
    _patches,
    _run_with,
)

AM2748_URL = "https://nebraskalegislature.gov/FloorDocs/109/PDF/AM/AM2748.pdf"
# The AM2748 shape: an amendment note that carries a long bill title. Constructed here (the real
# note is not in the repo); what matters is that the name built from it passes 255 bytes.
LONG_NOTE = "Amendment AM2748 to LB 1000 " + "A bill for an act relating to revenue and taxation; " * 6


def _bill():
    return _make_bill()


@pytest.mark.django_db
class TestArchiveFileNameLength:
    def test_a_long_note_gives_a_name_the_filesystem_accepts(self, tmp_path):
        path = _archive_path(_bill(), LONG_NOTE, "2026-01-05", AM2748_URL, "pdf")
        name = os.path.basename(path)

        assert len(name.encode()) <= 255
        # The real proof: the filesystem itself accepts the name.
        with open(tmp_path / name, "wb") as f:
            f.write(b"x")

    def test_the_long_note_name_still_ends_with_the_full_url_hash_and_extension(self):
        short = _archive_path(_bill(), "Introduced", "", AM2748_URL, "pdf")
        long_ = _archive_path(_bill(), LONG_NOTE, "", AM2748_URL, "pdf")

        url_hash_and_ext = os.path.basename(short).split("-", 1)[1]  # "<hash>.pdf"
        assert os.path.basename(long_).endswith("-" + url_hash_and_ext)

    def test_it_is_deterministic_and_two_urls_still_get_two_names(self):
        bill = _bill()
        a = _archive_path(bill, LONG_NOTE, "", AM2748_URL, "pdf")
        assert a == _archive_path(bill, LONG_NOTE, "", AM2748_URL, "pdf")
        assert a != _archive_path(bill, LONG_NOTE, "", AM2748_URL + "?v=2", "pdf")

    def test_a_short_name_is_exactly_what_it_was_before(self):
        bill = _bill()
        path = _archive_path(bill, "Introduced", "2026-01-05", "https://x.test/v1.pdf", "pdf")
        # sha256("https://x.test/v1.pdf")[:16], the pre-fix format date-note-hash.ext
        import hashlib

        h = hashlib.sha256(b"https://x.test/v1.pdf").hexdigest()[:16]
        assert os.path.basename(path) == f"2026-01-05-Introduced-{h}.pdf"

    def test_a_name_exactly_at_the_budget_is_untouched_and_one_more_is_trimmed(self):
        bill = _bill()
        budget = 200  # _MAX_ARCHIVE_FILE_NAME_BYTES
        fixed = len("-") + 16 + len(".pdf")  # "-<hash>.pdf", no date
        at_limit = "a" * (budget - fixed)
        over = "a" * (budget - fixed + 1)

        name_at = os.path.basename(_archive_path(bill, at_limit, "", AM2748_URL, "pdf"))
        name_over = os.path.basename(_archive_path(bill, over, "", AM2748_URL, "pdf"))

        assert len(name_at) == budget and name_at.startswith(at_limit + "-")
        assert len(name_over) == budget  # trimmed back to the budget, not left over it


@pytest.mark.django_db
class TestUnsavedDocumentsAreNamed:
    def _bill_with_link(self):
        bill = _make_bill()
        v = bill.versions.create(note="Introduced", date="")
        v.links.create(url=AM2748_URL, media_type="application/pdf")
        return bill

    def test_archive_bill_versions_collects_the_address_and_reason(self):
        bill = self._bill_with_link()
        unsaved = []

        _run_with(
            _patches(persist_raises=True), lambda: archive_bill_versions(bill, unsaved)
        )

        assert len(unsaved) == 1
        assert unsaved[0][0] == AM2748_URL
        assert "Permission denied" in unsaved[0][1]

    def test_the_archive_command_prints_every_unsaved_document_after_the_summary(self):
        from openstates.cli.text_extract import archive

        self._bill_with_link()
        with mock.patch("openstates.cli.text_extract.click.secho") as secho:
            _run_with(_patches(persist_raises=True), lambda: archive.callback(state="ak"))

        lines = [c.args[0] for c in secho.call_args_list]
        summary = next(i for i, t in enumerate(lines) if "persist_errors=1" in t)
        named = [i for i, t in enumerate(lines) if "not saved:" in t and AM2748_URL in t]
        assert len(named) == 1 and named[0] > summary
