"""OPEN-498: Massachusetts files agency reports, orders and amendment papers under bill numbers.

Their document has the same note ("Bill Text") as a real bill's, so the per-state note rules (OPEN-492)
cannot tell them apart; the bill's TITLE can. `is_bill_version(state, note, bill_title)` now marks a
Massachusetts document False unless its bill's title opens like a measure. The archiver and
`recompute-diff-order` both pass the title; this file proves both do, and that the rule is
Massachusetts-only and never hides a document because a title is missing.

The titles below are real ones from the 194th session (the local api-v3 archive, 2026-11-06).
"""

from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import recompute_diff_order
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import is_bill_version

MA = "ocd-jurisdiction/country:us/state:ma/government"
MI = "ocd-jurisdiction/country:us/state:mi/government"

NOT_BILLS = [
    # agency reports and letters filed under HD / SD numbers
    "A communication from the Executive Office of Public Safety and Security (see Section 2B(f) of Chapter 6A)",
    "The annual report of the Executive Office for Administration and Finance (under Section 2H of Chapter 7)",
    "Emergency Housing Assistance Program Bi-weekly Report",
    "Bridgewater State Hospital Complete Inspection Package 12-3-25",
    "Legislative recommendations of the Auditor of the Commonwealth",
    "PERAC regulatory filing 840 CMR 6.00",
    "REPORT of the SPECIAL JOINT COMMITTEE on INITIATIVE PETITIONS on the INITIATIVE PETITION of ANGIE ANDERSON",
    # legislative orders, including study orders titled only by their subject
    "Study Order",
    "Study Order- Time Expired",
    "Golf Carts",
    "authorizing the committee on Financial Services to make an investigation and study of certain House documents",
    "Municipalities and Regional Government - Extension Order",
    "Order relative to extending until Friday, February 27, 2026 the time in which the committee shall report",
    "Order relative to authorizing the joint committee on Public Health to make an investigation",
    # amendment papers, messages, rules
    "Amendment S.3290",
    "Governor's Message H.5712",
    "So much of the message from Her Excellency the Governor returning the General Appropriation Bill",
    "Proposed Senate Rules for the 2025-2026 Legislative Session",
    "Precept setting forth the Special Election to fill the vacancy of the First Middlesex Senatorial District",
]

REAL_MEASURES = [
    "An Act providing for line-of-duty death benefits for firefighters",
    "An Act to remove the 10 MW net metering cap on each municipality",
    "  an act relative to something (leading space, lower case)",
    "Resolve establishing a coastal waters wastewater financing commission",
    "Resolves providing for an investigation by a special commission",
    "A Resolve relative to the study of golf carts",
    "Resolutions rescinding previous Article V Convention applications",
    "Joint Resolution urging the United States Congress to act",
    "Proposal for a legislative amendment to the Constitution relative to voting rights",
]


@pytest.mark.parametrize("title", NOT_BILLS)
def test_a_massachusetts_report_order_or_amendment_is_not_a_bill_version(title):
    assert is_bill_version("Massachusetts", "Bill Text", title) is False


@pytest.mark.parametrize("title", REAL_MEASURES)
def test_a_massachusetts_measure_stays_a_bill_version(title):
    assert is_bill_version("Massachusetts", "Bill Text", title) is True


@pytest.mark.parametrize("title", [None, "", "   "])
def test_a_missing_title_never_hides_a_document(title):
    # Fails open: a caller with no title keeps the OPEN-492 answer.
    assert is_bill_version("Massachusetts", "Bill Text", title) is True


@pytest.mark.parametrize("state", ["Michigan", "Florida", "Virginia", "Nebraska"])
def test_the_title_rule_applies_to_massachusetts_only(state):
    assert is_bill_version(state, "Introduced", "Study Order") is True


def test_the_note_rules_still_decide_for_states_that_have_them():
    # Virginia's note rule wins even with a real-looking title; no title is needed for it.
    assert (
        is_bill_version(
            "Virginia", "Courts of Justice Amendment", "An Act relating to taxes"
        )
        is False
    )
    assert is_bill_version("Virginia", "Courts of Justice Amendment") is False


TEXT = "Ordered, That the committee on Transportation be authorized to sit during a recess of the General Court.\n"
URL = "https://malegislature.gov/Bills/194/H4727.pdf"


def _ma_bill(title):
    bill = _make_bill(jid=MA, jurisdiction_name="Massachusetts")
    bill.title = title
    bill.classification = ["bill"]
    bill.save()
    bill.versions.create(note="Bill Text", date="").links.create(
        url=URL, media_type="application/pdf"
    )
    return bill


@pytest.mark.django_db
@pytest.mark.parametrize(
    "title, expected",
    [("Golf Carts", False), ("An Act relative to golf carts", True)],
)
def test_the_archiver_stores_the_answer_using_the_bills_title(title, expected):
    bill = _ma_bill(title)
    _archive(bill, {URL: TEXT})
    assert BillVersionDocument.objects.get(bill=bill).is_bill_version is expected


@pytest.mark.django_db
def test_a_michigan_bill_with_the_same_title_is_still_stored_true():
    bill = _make_bill(jid=MI, jurisdiction_name="Michigan")
    bill.title = "Golf Carts"
    bill.save()
    bill.versions.create(note="Bill Text", date="").links.create(
        url=URL, media_type="application/pdf"
    )
    _archive(bill, {URL: TEXT})
    assert BillVersionDocument.objects.get(bill=bill).is_bill_version is True


def _recompute(bill, *args):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid",
        return_value=bill.legislative_session.jurisdiction_id,
    ):
        result = CliRunner().invoke(recompute_diff_order, ["ma", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _saved(bill, flag=None):
    return BillVersionDocument.objects.create(
        bill=bill,
        version_note="Bill Text",
        version_date="",
        source_url=URL,
        media_type="application/pdf",
        raw_text=TEXT,
        archive_location="s3://bucket/key",
        is_bill_version=flag,
    )


@pytest.mark.django_db
def test_recompute_dry_run_counts_a_report_and_writes_nothing():
    bill = _ma_bill(
        "A communication from the Department of Transitional Assistance (see Section 2)"
    )
    doc = _saved(bill)

    out = _recompute(bill, "--dry-run")

    assert "[DRY RUN] is_bill_version: set=1 changed=0 not_a_version=1" in out
    doc.refresh_from_db()
    assert doc.is_bill_version is None


@pytest.mark.django_db
def test_recompute_commit_marks_a_report_false_and_a_second_run_changes_nothing():
    report = _ma_bill("Study Order")
    doc = _saved(report)

    out = _recompute(report, "--commit")

    assert "[COMMITTED] is_bill_version: set=1 changed=0 not_a_version=1" in out
    doc.refresh_from_db()
    assert doc.is_bill_version is False
    assert doc.raw_text == TEXT and doc.archive_location == "s3://bucket/key"
    assert "is_bill_version: set=0 changed=0 not_a_version=1" in _recompute(
        report, "--commit"
    )


@pytest.mark.django_db
def test_recompute_commit_fills_a_real_measure_as_true():
    measure = _ma_bill("An Act relative to golf carts")
    doc = _saved(measure)

    out = _recompute(measure, "--commit")

    assert "is_bill_version: set=1 changed=0 not_a_version=0" in out
    doc.refresh_from_db()
    assert doc.is_bill_version is True


@pytest.mark.django_db
def test_recompute_corrects_a_value_stored_before_the_rule_existed():
    report = _ma_bill("Amendment S.3290")
    doc = _saved(report, flag=True)

    out = _recompute(report, "--commit")

    assert "is_bill_version: set=0 changed=1 not_a_version=1" in out
    doc.refresh_from_db()
    assert doc.is_bill_version is False


@pytest.mark.django_db
def test_recompute_restores_true_for_a_measure_wrongly_stored_false():
    """The rollback path: if a rule is loosened or reverted, rerunning the recompute clears the mark."""
    measure = _ma_bill("An Act relative to golf carts")
    doc = _saved(measure, flag=False)

    out = _recompute(measure, "--commit")

    assert "is_bill_version: set=0 changed=1 not_a_version=0" in out
    doc.refresh_from_db()
    assert doc.is_bill_version is True
