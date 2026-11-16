"""OPEN-564: Alabama's 11-15 archive saved 456 papers a second time under new version notes.

`os-text-extract remove-stale-documents <state>` removes a saved document whose version note no longer exists on
its bill, only where a current document keeps the same file (same bill, address and `sha256_hash`). The last saved
copy of a file is never removed.

The one short committee amendment among the 200 papers that became versions (HB 248's "Replace line 54 ...") is
marked not-a-version by its text (`is_amendment_text`), and the full substitutes stay versions (Ramon, 2026-11-15).
The texts below are real ones from the Mac replica (Alabama 2026), cut short where long.
"""

import csv
from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import (
    recompute_diff_order,
    recomputed_diffs_for_documents,
    remove_stale_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import is_amendment_text, is_bill_version

AL = "ocd-jurisdiction/country:us/state:al/government"

# HB 248, "Reported Out of Committee Second House" (2J5RBII-1.pdf), 1,305 characters in all.
HB248_AMENDMENT = (
    "Replace line 54 on page 2 with the following:\n"
    "harbors; to authorize existing nonprofit corporations\n"
    "to opt out of the amendatory provisions of this act\n"
    "pertaining to Chapter 3A of Title 10A, Code of Alabama\n"
    "1975; and to repeal Sections 10A-2A-16.10 and\n"
    "10A-3A-4.20,\n"
    "Replace line 3297 on page 118 with the following:\n"
    "Section 7. A nonprofit corporation governed by\n"
    "Chapter 3A of Title 10A, Code of Alabama 1975, and in\n"
    "Section 8. This act shall become effective on August\n"
    "1,\n"
)
# HB 87, "Ways and Means Education 1st Substitute", whole text: a short bill, but a whole one.
HB87_SUBSTITUTE = (
    "SYNOPSIS:\n"
    "Under existing law, the gross proceeds from the\n"
    "sale of certain items are exempt from sales and use\n"
    "tax.\n"
    "A BILL\n"
    "TO BE ENTITLED\n"
    "AN ACT\n"
    "Relating to sales and use tax; to exempt the gross\n"
    "proceeds from the sale of dried corn sold as deer feed from\n"
    "state sales and use taxes.\n"
    "BE IT ENACTED BY THE LEGISLATURE OF ALABAMA:\n"
    "Section 1. (a) For the purposes of this section, the\n"
    'term "deer corn" shall mean any shelled corn sold.\n'
    "Section 2. This act shall become effective on September\n"
    "1, 2026.\n"
)
# HB 511, Enrolled: a proposed constitutional amendment, no enacting clause, and a real version.
HB511_ENROLLED = (
    "HB511\n"
    "FKVHWDC-3\n"
    "By Representative Ingram   (Constitutional Amendment)\n"
    "Enrolled, An Act,\n"
    "Proposing an amendment to the Constitution of Alabama\n"
    "of 2022, to require local boards of education to adopt\n"
    "Section 1. The following amendment to the Constitution\n"
    "of Alabama of 2022, is proposed:\n"
)


# ---- is_amendment_text / is_bill_version -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "state, text, expected",
    [
        ("Alabama", HB248_AMENDMENT, True),
        ("Alabama", "\n  " + HB248_AMENDMENT, True),
        ("Alabama", "Delete lines 4 through 9 on page 1.\n", True),
        ("Alabama", HB87_SUBSTITUTE, False),
        ("Alabama", HB511_ENROLLED, False),
        # opens like an edit but carries a whole bill: a substitute, so a version
        ("Alabama", "Amend the bill as follows.\n" + HB87_SUBSTITUTE, False),
        ("Alabama", "", False),
        ("Alabama", None, False),
        # no text rule outside Alabama
        ("Michigan", HB248_AMENDMENT, False),
    ],
)
def test_amendment_text_rule(state, text, expected):
    assert is_amendment_text(state, text) is expected


def test_is_bill_version_reads_the_text_only_when_given():
    note = "Reported Out of Committee Second House"
    assert is_bill_version("Alabama", note) is True
    assert is_bill_version("Alabama", note, None, HB87_SUBSTITUTE) is True
    assert is_bill_version("Alabama", note, None, HB248_AMENDMENT) is False


def test_an_amendment_text_takes_no_diff_and_is_not_the_next_baseline():
    """The amendment sits between two full texts; the full text after it diffs against the one before it."""

    class Doc:
        def __init__(self, note, text):
            self.version_note, self.version_date = note, ""
            self.source_url = f"https://x.test/{note}.pdf"
            self.media_type = "application/pdf"
            self.raw_text, self.is_error = text, False
            self.diff_from_previous_version = None

    intro = Doc("Introduced", HB87_SUBSTITUTE)
    amendment = Doc("Reported Out of Committee Second House", HB248_AMENDMENT)
    enrolled = Doc("Enrolled", HB87_SUBSTITUTE.replace("September", "October"))
    result = recomputed_diffs_for_documents(
        [intro, amendment, enrolled],
        jurisdiction_name="Alabama",
        is_bill=True,
        bill_identifier="HB 248",
    )
    diffs = {d.version_note: diff for d, diff in result["changed"]}
    assert amendment not in [d for d, _ in result["changed"]]  # stays None
    assert "-Section 2. This act shall become effective on September" in diffs["Enrolled"]
    assert "Replace line" not in diffs["Enrolled"]


# ---- remove-stale-documents ------------------------------------------------------------------------------------


def _al_bill(identifier="HB 248", notes=("Introduced", "Reported Out of Committee Second House")):
    bill = _make_bill(jid=AL, jurisdiction_name="Alabama")
    bill.identifier = identifier
    bill.save()
    for note in notes:
        bill.versions.create(note=note, date="")
    return bill


def _doc(bill, note, url, sha, **extra):
    return BillVersionDocument.objects.create(
        bill=bill,
        version_note=note,
        version_date="",
        source_url=url,
        media_type="application/pdf",
        raw_text=extra.pop("raw_text", "text"),
        sha256_hash=sha,
        archive_location="s3://bucket/key",
        **extra,
    )


PAPER = "https://x.test/2J5RBII-1.pdf"


@pytest.fixture
def hb248(db):
    """HB 248 as on the replica: the floor-motion name (stale) and the committee-report name (current), one file."""
    bill = _al_bill()
    intro = _doc(bill, "Introduced", "https://x.test/HB248-int.pdf", "a" * 64)
    stale = _doc(
        bill,
        "Givhan motion to Adopt - Adopted Roll Call 1272",
        PAPER,
        "b" * 64,
        is_bill_version=False,
    )
    current = _doc(
        bill, "Reported Out of Committee Second House", PAPER, "b" * 64, is_bill_version=True
    )
    return bill, intro, stale, current


def _run(*args, state="al"):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=AL
    ):
        return CliRunner().invoke(remove_stale_documents, [state, *args])


def _ok(*args):
    result = _run(*args)
    assert result.exit_code == 0, result.output
    return result.output


def _ids(bill):
    return set(BillVersionDocument.objects.filter(bill=bill).values_list("id", flat=True))


@pytest.mark.django_db
def test_dry_run_counts_and_writes_nothing(hb248, tmp_path):
    bill, intro, stale, current = hb248
    listing = tmp_path / "plan.csv"
    out = _ok("--dry-run", "--list", str(listing))
    assert "[DRY RUN] documents=3 stale=1 removed=1 kept_last_copy=0 kept_no_file_hash=0" in out
    assert "after: documents=2 distinct (bill, address)=2 removed_versions=0" in out
    assert _ids(bill) == {intro.id, stale.id, current.id}
    rows = list(csv.DictReader(listing.open()))
    assert [(int(r["removed_id"]), int(r["kept_id"])) for r in rows] == [(stale.id, current.id)]


@pytest.mark.django_db
def test_commit_removes_the_stale_copy_and_keeps_the_current_one(hb248, tmp_path):
    bill, intro, stale, current = hb248
    listing = tmp_path / "done.csv"
    out = _ok("--commit", "--list", str(listing), "--expect", "1")
    assert "[COMMITTED] documents=3 stale=1 removed=1" in out
    assert _ids(bill) == {intro.id, current.id}
    assert listing.exists() and not (tmp_path / "done.csv.partial").exists()
    # a second run finds nothing
    assert "stale=0 removed=0" in _ok("--commit", "--list", str(tmp_path / "again.csv"))


@pytest.mark.django_db
def test_the_last_copy_of_a_file_is_never_removed(db, tmp_path):
    """Stale rows whose file no current row keeps: no copy at all, a different file at the same address, the same
    file on another bill, and two stale rows that only have each other. None may go."""
    bill = _al_bill(notes=("Introduced",))
    other = _al_bill(identifier="HB 249", notes=("Introduced",))
    _doc(bill, "Introduced", "https://x.test/i.pdf", "a" * 64)
    lone = _doc(bill, "Old name", "https://x.test/lone.pdf", "c" * 64)
    _doc(bill, "Introduced", "https://x.test/changed.pdf", "d" * 64)
    changed = _doc(bill, "Old name", "https://x.test/changed.pdf", "e" * 64)
    _doc(other, "Introduced", "https://x.test/shared.pdf", "f" * 64)
    elsewhere = _doc(bill, "Old name", "https://x.test/shared.pdf", "f" * 64)
    pair_a = _doc(bill, "Old name A", "https://x.test/pair.pdf", "9" * 64)
    pair_b = _doc(bill, "Old name B", "https://x.test/pair.pdf", "9" * 64)
    no_hash = _doc(bill, "Old name", "https://x.test/nohash.pdf", None)

    out = _ok("--commit", "--list", str(tmp_path / "l.csv"))
    assert "stale=6 removed=0 kept_last_copy=5 kept_no_file_hash=1" in out
    remaining = _ids(bill)
    for doc in (lone, changed, elsewhere, pair_a, pair_b, no_hash):
        assert doc.id in remaining


@pytest.mark.django_db
def test_two_stale_names_for_one_current_file_both_go(db, tmp_path):
    bill = _al_bill(notes=("Reported Out of Committee House of Origin",))
    keep = _doc(bill, "Reported Out of Committee House of Origin", PAPER, "b" * 64)
    _doc(bill, "Motion to Adopt - Adopted Roll Call 1082", PAPER, "b" * 64)
    _doc(bill, "Orr motion to Table - Adopted Voice Vote", PAPER, "b" * 64)
    _ok("--commit", "--list", str(tmp_path / "l.csv"))
    assert _ids(bill) == {keep.id}


@pytest.mark.django_db
def test_commit_needs_a_list_and_the_agreed_count(hb248, tmp_path):
    bill = hb248[0]
    before = _ids(bill)
    result = _run("--commit")
    assert result.exit_code != 0 and "--commit needs --list" in result.output
    listing = tmp_path / "l.csv"
    result = _run("--commit", "--list", str(listing), "--expect", "456")
    assert result.exit_code != 0 and "not the expected 456" in result.output
    assert _ids(bill) == before
    assert not listing.exists() and not (tmp_path / "l.csv.partial").exists()


@pytest.mark.django_db
def test_a_change_between_read_and_write_rolls_everything_back(hb248, tmp_path):
    """The plan is made again inside the transaction; if it differs, nothing is removed and no list is left."""
    bill = hb248[0]
    before = _ids(bill)
    from openstates.cli import text_extract

    real = text_extract._stale_document_plan
    calls = []

    def second_read_differs(bills):
        rows, removals, kept = real(bills)
        calls.append(1)
        return (rows, removals, kept) if len(calls) == 1 else (rows, [], kept)

    with mock.patch.object(text_extract, "_stale_document_plan", side_effect=second_read_differs):
        result = _run("--commit", "--list", str(tmp_path / "l.csv"))
    assert result.exit_code != 0 and "changed since they were read" in result.output
    assert _ids(bill) == before
    assert not (tmp_path / "l.csv").exists() and not (tmp_path / "l.csv.partial").exists()


@pytest.mark.django_db
def test_an_earlier_list_is_never_overwritten(hb248, tmp_path):
    bill = hb248[0]
    before = _ids(bill)
    for existing in ("l.csv", "l.csv.partial"):
        path = tmp_path / existing
        path.write_text("earlier record")
        result = _run("--commit", "--list", str(tmp_path / "l.csv"))
        assert result.exit_code != 0 and "already exists" in result.output
        assert path.read_text() == "earlier record"
        path.unlink()
    assert _ids(bill) == before


@pytest.mark.django_db
def test_a_version_renamed_between_read_and_write_rolls_back(hb248, tmp_path):
    """The current note of the kept copy goes away after the read: its stale twin is no longer safe to remove."""
    bill, intro, stale, current = hb248
    before = _ids(bill)
    from openstates.cli import text_extract

    real = text_extract._stale_document_plan
    calls = []

    def rename_after_first_read(bills):
        result = real(bills)
        calls.append(1)
        if len(calls) == 1:
            bill.versions.filter(note="Reported Out of Committee Second House").update(note="Renamed")
        return result

    with mock.patch.object(text_extract, "_stale_document_plan", side_effect=rename_after_first_read):
        result = _run("--commit", "--list", str(tmp_path / "l.csv"))
    assert result.exit_code != 0 and "changed since they were read" in result.output
    assert _ids(bill) == before


@pytest.mark.django_db
def test_session_scopes_the_run(hb248, tmp_path):
    bill = hb248[0]
    out = _ok("--session", "1999", "--dry-run")
    assert "documents=0 stale=0 removed=0" in out
    assert len(_ids(bill)) == 3


# ---- recompute-diff-order marks the amendment ------------------------------------------------------------------


@pytest.mark.django_db
def test_recompute_marks_the_amendment_not_a_version_and_drops_its_diff(db):
    bill = _al_bill(notes=("Introduced", "Reported Out of Committee Second House"))
    _doc(bill, "Introduced", "https://x.test/i.pdf", "a" * 64, raw_text=HB87_SUBSTITUTE, is_bill_version=True)
    amendment = _doc(
        bill,
        "Reported Out of Committee Second House",
        PAPER,
        "b" * 64,
        raw_text=HB248_AMENDMENT,
        is_bill_version=True,
        diff_from_previous_version="@@ -1,3054 +1,27 @@",
    )
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=AL
    ):
        result = CliRunner().invoke(recompute_diff_order, ["al", "--commit"])
    assert result.exit_code == 0, result.output
    assert "is_bill_version: set=0 changed=1 not_a_version=1" in result.output
    amendment.refresh_from_db()
    assert amendment.is_bill_version is False
    assert amendment.diff_from_previous_version is None


@pytest.mark.django_db
def test_the_archiver_stores_the_text_answer_and_skips_the_amendment_in_the_lineage(db):
    bill = _al_bill(notes=())
    for note, url in (
        ("Introduced", "https://x.test/i.pdf"),
        ("Reported Out of Committee Second House", PAPER),
        ("Enrolled", "https://x.test/e.pdf"),
    ):
        bill.versions.create(note=note, date="").links.create(url=url, media_type="application/pdf")
    _archive(
        bill,
        {
            "https://x.test/i.pdf": HB87_SUBSTITUTE,
            PAPER: HB248_AMENDMENT,
            "https://x.test/e.pdf": HB87_SUBSTITUTE.replace("September", "October"),
        },
    )
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    paper = docs["Reported Out of Committee Second House"]
    assert paper.is_bill_version is False and paper.diff_from_previous_version is None
    assert docs["Introduced"].is_bill_version is True
    enrolled = docs["Enrolled"].diff_from_previous_version
    assert "-Section 2. This act shall become effective on September" in enrolled
    assert "Replace line" not in enrolled
