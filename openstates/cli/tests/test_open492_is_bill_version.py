"""OPEN-492: the archiver decides once whether a saved document is a real bill version.

The flag (`BillVersionDocument.is_bill_version`) is written when the archiver saves a document and
filled in for already-saved documents by `recompute-diff-order <state> --commit`. The rules are the
per-state ones in openstates/utils/version_ordering.py; this file proves the answer is stored, not
the rules themselves (those are tested in test_version_ordering.py and the per-state OPEN-224 files).
"""

from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import recompute_diff_order
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import is_bill_version

NE = "ocd-jurisdiction/country:us/state:ne/government"
MI = "ocd-jurisdiction/country:us/state:mi/government"


@pytest.mark.parametrize(
    "state, note, expected",
    [
        # states with rules: the paper is not a version, the real versions are
        ("Virginia", "Courts of Justice Amendment", False),
        ("Virginia", "Courts of Justice Substitute", True),
        ("Virginia", "Introduced", True),
        ("Utah", "Comparison to Sub #3", False),
        ("Utah", "Substitute #3", True),
        ("Arizona", "SENATE - Ortiz flr amend (ref Resolution) failed", False),
        ("Arizona", "House Engrossed Version", True),
        ("Nebraska", "Judiciary AM12", False),
        ("Nebraska", "Enrollment and Review ER5", False),
        ("Nebraska", "Slip Law", True),
        ("Louisiana", "House Committee Amendment, #12, COM, Draft", False),
        ("Louisiana", "Senate Floor Bureau Note, #2676, Cloud, Adopted", False),
        (
            "Louisiana",
            "Conference Committee Report, #4594, House Adopted, Senate Adopted",
            False,
        ),
        ("Louisiana", "HB374 Enrolled", True),
        ("Alabama", "Kelley motion to Adopt - Adopted Roll Call 686", False),
        ("Alabama", "Judiciary 1st Substitute", True),
        # a state with no rules: everything is a version, even a note another state excludes
        ("Michigan", "Conference Report", True),
        ("Michigan", "Introduced", True),
    ],
)
def test_the_stored_answer_follows_the_per_state_rules(state, note, expected):
    assert is_bill_version(state, note) is expected


INTRODUCED = "A BILL FOR AN ACT relating to taxes.\nSection 1. A court may consider the award.\n"
PAPER = "AM12\nStrike the enacting clause and insert a different bill entirely.\n"


def _ne_bill():
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")
    bill.classification = ["bill"]
    bill.save()
    for note, url in (("Introduced", "https://x.test/i.pdf"), ("Judiciary AM12", "https://x.test/p.pdf")):
        bill.versions.create(note=note, date="").links.create(
            url=url, media_type="application/pdf"
        )
    return bill


@pytest.mark.django_db
def test_the_archiver_stores_the_answer_on_every_new_document():
    bill = _ne_bill()
    _archive(bill, {"https://x.test/i.pdf": INTRODUCED, "https://x.test/p.pdf": PAPER})
    flags = {
        d.version_note: d.is_bill_version
        for d in BillVersionDocument.objects.filter(bill=bill)
    }
    assert flags == {"Introduced": True, "Judiciary AM12": False}


@pytest.mark.django_db
def test_a_state_with_no_rules_stores_true_for_a_note_other_states_exclude():
    bill = _make_bill(jid=MI, jurisdiction_name="Michigan")
    bill.versions.create(note="Conference Report", date="").links.create(
        url="https://x.test/c.pdf", media_type="application/pdf"
    )
    _archive(bill, {"https://x.test/c.pdf": INTRODUCED})
    doc = BillVersionDocument.objects.get(bill=bill)
    assert doc.is_bill_version is True


@pytest.mark.django_db
def test_the_archiver_leaves_an_already_archived_row_alone():
    """The skip path stays a cheap check: filling old rows is the recompute's job, with a go."""
    bill = _ne_bill()
    old = BillVersionDocument.objects.create(
        bill=bill,
        version_note="Judiciary AM12",
        version_date="",
        source_url="https://x.test/p.pdf",
        media_type="application/pdf",
        raw_text=PAPER,
        archive_location="s3://bucket/key",
        is_bill_version=None,
    )
    _archive(bill, {"https://x.test/i.pdf": INTRODUCED, "https://x.test/p.pdf": PAPER})
    old.refresh_from_db()
    assert old.is_bill_version is None


def _recompute(bill, *args):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid",
        return_value=bill.legislative_session.jurisdiction_id,
    ):
        result = CliRunner().invoke(recompute_diff_order, ["ne", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _saved_rows(bill, intro_flag=None, paper_flag=None):
    mk = lambda note, url, text, flag: BillVersionDocument.objects.create(  # noqa: E731
        bill=bill,
        version_note=note,
        version_date="",
        source_url=url,
        media_type="application/pdf",
        raw_text=text,
        archive_location="s3://bucket/key",
        is_bill_version=flag,
    )
    return (
        mk("Introduced", "https://x.test/i.pdf", INTRODUCED, intro_flag),
        mk("Judiciary AM12", "https://x.test/p.pdf", PAPER, paper_flag),
    )


@pytest.mark.django_db
def test_recompute_dry_run_reports_counts_and_writes_nothing():
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")
    intro, paper = _saved_rows(bill)

    out = _recompute(bill, "--dry-run")

    assert "[DRY RUN] is_bill_version: set=2 changed=0 not_a_version=1" in out
    intro.refresh_from_db()
    paper.refresh_from_db()
    assert intro.is_bill_version is None and paper.is_bill_version is None


@pytest.mark.django_db
def test_recompute_commit_fills_the_flag_and_a_second_run_changes_nothing():
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")
    intro, paper = _saved_rows(bill)

    out = _recompute(bill, "--commit")
    assert "[COMMITTED] is_bill_version: set=2 changed=0 not_a_version=1" in out
    intro.refresh_from_db()
    paper.refresh_from_db()
    assert (intro.is_bill_version, paper.is_bill_version) == (True, False)
    # nothing else on the row moved
    assert paper.raw_text == PAPER and paper.archive_location == "s3://bucket/key"

    again = _recompute(bill, "--commit")
    assert "is_bill_version: set=0 changed=0 not_a_version=1" in again


@pytest.mark.django_db
def test_recompute_corrects_a_wrong_stored_value():
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")
    # stored True for the paper (as if the rule was added after it was saved)
    intro, paper = _saved_rows(bill, intro_flag=True, paper_flag=True)

    out = _recompute(bill, "--commit")

    assert "is_bill_version: set=0 changed=1 not_a_version=1" in out
    paper.refresh_from_db()
    assert paper.is_bill_version is False


FINAL_READING = "A BILL FOR AN ACT relating to taxes.\nSection 1. A court shall consider the award.\n"


@pytest.mark.django_db
def test_recompute_saves_a_changed_diff_and_a_filled_flag_for_the_same_document():
    """Both fields change on one row in one run: both must be in the database afterwards."""
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")

    def mk(note, url, text, diff):
        return BillVersionDocument.objects.create(
            bill=bill,
            version_note=note,
            version_date="",
            source_url=url,
            media_type="application/pdf",
            raw_text=text,
            archive_location="s3://bucket/key",
            diff_from_previous_version=diff,
            is_bill_version=None,
        )

    # Introduced is first in the true order, so its stored diff is wrong and must be cleared;
    # Final Reading has no stored diff and must get one. Neither has a flag yet.
    intro = mk("Introduced", "https://x.test/i.pdf", INTRODUCED, "WRONG-BACKWARD-DIFF")
    final = mk("Final Reading", "https://x.test/f.pdf", FINAL_READING, None)

    out = _recompute(bill, "--commit")

    assert "corrected=1 nulled=1" in out
    assert "is_bill_version: set=2 changed=0 not_a_version=0" in out
    intro.refresh_from_db()
    final.refresh_from_db()
    assert intro.diff_from_previous_version is None and intro.is_bill_version is True
    assert "shall consider" in final.diff_from_previous_version
    assert final.is_bill_version is True
