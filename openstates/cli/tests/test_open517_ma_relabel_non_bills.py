"""OPEN-517: Massachusetts papers filed under bill numbers get a label that says what they are.

Ramon (2026-11-09): relabel, never delete, a label per kind, undoable. `non_bill_kind()` names the kind
for a bill the OPEN-498 title rule already calls not-a-bill; `os-text-extract relabel-non-bills` writes
it, keeps the old label in extras, and leaves amendment papers and anything with a recorded vote alone.

Titles are real ones from the 194th session (Mac replica, 2026-11-09).
"""

import csv
from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import ORIGINAL_CLASSIFICATION_KEY, relabel_non_bills
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import Bill, VoteEvent
from openstates.utils.version_ordering import NON_BILL_AMENDMENT, non_bill_kind

MA = "ocd-jurisdiction/country:us/state:ma/government"
MI = "ocd-jurisdiction/country:us/state:mi/government"

KINDS = [
    ("Middlesex County Restoration Center Commission Eighth Annual Report", "report"),
    (
        "A monthly report of the Department of Revenue (under Section 5(e) of Chapter 62F)",
        "report",
    ),
    ("Bridgewater State Hospital Complete Inspection Package 12-3-25", "report"),
    ("Legislative recommendations of the Auditor of the Commonwealth", "report"),
    ("PERAC regulatory filing 840 CMR 6.00", "report"),
    (
        "A communication from the Office of the Comptroller (see item 1595-7066)",
        "communication",
    ),
    (
        "Communication from the Massachusetts Gaming Commission (pursuant to Section 9B)",
        "communication",
    ),
    ("Governor's Message H.5712", "communication"),
    (
        "So much of the message from Her Excellency the Governor returning the General Appropriation Bill",
        "communication",
    ),
    ("Governor Healey's State of the Commonwealth Address", "communication"),
    ("Study Order", "study request"),
    (
        "Order relative to authorizing the joint committee on Financial Services to make an investigation and study",
        "study request",
    ),
    (
        "Order relative to extending until Friday, February 27, 2026 the time in which the committee shall report",
        "order",
    ),
    (
        "Order for considering Senate Bill, No. 2537 during the Senate Session of July 17",
        "order",
    ),
    ("Revenue - Extension Order", "order"),
    ("Extension Order H.4819", "order"),
    (
        "Precept setting forth the Special Election to fill the vacancy of the First Middlesex Senatorial District",
        "order",
    ),
    ("Amendment S.3290", NON_BILL_AMENDMENT),
]


@pytest.mark.parametrize("title, kind", KINDS)
def test_each_kind_of_massachusetts_paper_gets_its_label(title, kind):
    assert non_bill_kind("Massachusetts", title) == kind


def test_a_committee_study_order_titled_by_its_subject_is_known_by_its_action():
    actions = [
        "Reported from the committee on Transportation",
        "Order reported favorably by committee and referred to the committee on Joint Rules",
    ]
    assert (
        non_bill_kind("Massachusetts", "rail and transit", actions) == "study request"
    )
    # without that action the same title is just a filed paper
    assert non_bill_kind("Massachusetts", "rail and transit", actions[:1]) == "report"


@pytest.mark.parametrize(
    "title",
    [
        "An Act relative to the annual report of the Department of Revenue",
        "Resolve providing for an investigation and study by a special commission",
        "Resolutions urging the Governor to send a communication to Congress",
        None,
        "",
        "   ",
    ],
)
def test_a_real_measure_or_a_missing_title_never_gets_a_kind(title):
    assert non_bill_kind("Massachusetts", title) is None


def test_the_kinds_are_massachusetts_only():
    assert non_bill_kind("Michigan", "Study Order") is None


def _ma_bill(
    title, identifier, classification=("proposed bill",), jid=MA, name="Massachusetts"
):
    bill = _make_bill(jid=jid, jurisdiction_name=name)
    bill.identifier = identifier
    bill.title = title
    bill.classification = list(classification)
    bill.save()
    return bill


def _run(*args, jid=MA, state="ma"):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=jid
    ):
        result = CliRunner().invoke(relabel_non_bills, [state, *args])
    return result


def _ok(*args, **kwargs):
    result = _run(*args, **kwargs)
    assert result.exit_code == 0, result.output
    return result.output


def _fresh(bill):
    return Bill.objects.get(pk=bill.pk)


@pytest.fixture
def ma_items(db):
    # one session per _make_bill call, so each bill sits in its own; the command reads them all
    report = _ma_bill(
        "Middlesex County Restoration Center Commission Eighth Annual Report", "SD 4338"
    )
    order = _ma_bill("Revenue - Extension Order", "S 3173", classification=("bill",))
    measure = _ma_bill(
        "An Act relative to golf carts", "H 4727", classification=("bill",)
    )
    amendment = _ma_bill("Amendment S.3290", "S 3290", classification=("bill",))
    rules = _ma_bill(
        "Proposed Senate Rules for the 2025-2026 Legislative Session",
        "S 14",
        classification=("bill",),
    )
    VoteEvent.objects.create(
        bill=rules,
        legislative_session=rules.legislative_session,
        motion_text="adopted",
        organization=rules.from_organization,
        start_date="2025-02-06",
        result="pass",
    )
    return dict(
        report=report, order=order, measure=measure, amendment=amendment, rules=rules
    )


def test_the_dry_run_counts_by_label_and_writes_nothing(ma_items, tmp_path):
    before = {k: (list(b.classification), dict(b.extras)) for k, b in ma_items.items()}
    listing = tmp_path / "list.csv"

    out = _ok("--dry-run", "--list", str(listing))

    assert "[DRY RUN] relabelled=2 already_labelled=0" in out
    assert "skipped_amendment_paper=1" in out and "skipped_has_recorded_votes=1" in out
    assert "proposed bill -> report: 1" in out and "bill -> order: 1" in out
    for key, bill in ma_items.items():
        fresh = _fresh(bill)
        assert (list(fresh.classification), dict(fresh.extras)) == before[key]
    rows = list(csv.DictReader(listing.open()))
    assert {(r["identifier"], r["old"], r["new"]) for r in rows} == {
        ("SD 4338", "proposed bill", "report"),
        ("S 3173", "bill", "order"),
    }


def test_commit_relabels_keeps_the_old_label_and_leaves_the_rest(ma_items):
    stamps = {k: _fresh(b).updated_at for k, b in ma_items.items()}

    out = _ok("--commit")

    assert "[COMMITTED] relabelled=2" in out
    report, order = _fresh(ma_items["report"]), _fresh(ma_items["order"])
    assert report.classification == ["report"]
    assert report.extras[ORIGINAL_CLASSIFICATION_KEY] == ["proposed bill"]
    assert order.classification == ["order"]
    assert order.extras[ORIGINAL_CLASSIFICATION_KEY] == ["bill"]
    # a real bill, an amendment paper and an item with a recorded vote are untouched
    for key in ("measure", "amendment", "rules"):
        fresh = _fresh(ma_items[key])
        assert fresh.classification == list(ma_items[key].classification)
        assert ORIGINAL_CLASSIFICATION_KEY not in fresh.extras
    # nothing downstream is told the bills changed
    assert {k: _fresh(b).updated_at for k, b in ma_items.items()} == stamps
    # a second run finds nothing to do and keeps the scraper's original label
    assert "relabelled=0 already_labelled=2" in _ok("--commit")
    assert _fresh(ma_items["report"]).extras[ORIGINAL_CLASSIFICATION_KEY] == [
        "proposed bill"
    ]


def test_undo_puts_the_old_label_back(ma_items):
    bill = ma_items["report"]
    bill.extras = {"keep": "me"}
    bill.save()
    _ok("--commit")

    assert "[DRY RUN] restored=2" in _ok("--undo")
    assert _fresh(bill).classification == ["report"]

    assert "[COMMITTED] restored=2" in _ok("--undo", "--commit")
    fresh = _fresh(bill)
    assert fresh.classification == ["proposed bill"]
    assert fresh.extras == {"keep": "me"}
    assert _fresh(ma_items["order"]).classification == ["bill"]


def test_expect_aborts_before_any_write_when_the_count_moved(ma_items):
    result = _run("--commit", "--expect", "3")

    assert result.exit_code != 0
    assert "2 bills would change, not the expected 3; nothing written" in result.output
    assert _fresh(ma_items["report"]).classification == ["proposed bill"]
    assert "[COMMITTED] relabelled=2" in _ok("--commit", "--expect", "2")


def test_another_state_is_never_touched(ma_items):
    mi = _ma_bill(
        "Study Order", "HB 1", classification=("bill",), jid=MI, name="Michigan"
    )

    assert "relabelled=0" in _ok("--commit", jid=MI, state="mi")
    _ok("--commit")

    assert _fresh(mi).classification == ["bill"]
    assert ORIGINAL_CLASSIFICATION_KEY not in _fresh(mi).extras


@pytest.mark.parametrize("drift", ["vote", "classification", "extras"])
def test_a_row_that_changes_mid_run_aborts_the_whole_run(ma_items, drift):
    from openstates.cli import text_extract

    report = ma_items["report"]
    real_kind = text_extract._non_bill_kind

    def kind_then_drift(name, title, actions):
        kind = real_kind(name, title, actions)
        if title == report.title:
            # an import or a vote lands after the row was read, before it is written
            if drift == "vote":
                VoteEvent.objects.create(
                    bill=report,
                    legislative_session=report.legislative_session,
                    motion_text="adopted",
                    organization=report.from_organization,
                    start_date="2026-11-10",
                    result="pass",
                )
            elif drift == "classification":
                Bill.objects.filter(pk=report.pk).update(classification=["bill"])
            else:
                Bill.objects.filter(pk=report.pk).update(extras={"new": "key"})
        return kind

    with mock.patch.object(text_extract, "_non_bill_kind", kind_then_drift):
        result = _run("--commit")

    assert result.exit_code != 0
    assert "changed since it was read; rolled back, nothing written" in result.output
    assert _fresh(ma_items["order"]).classification == ["bill"]
    assert ORIGINAL_CLASSIFICATION_KEY not in _fresh(ma_items["order"]).extras
    assert ORIGINAL_CLASSIFICATION_KEY not in _fresh(report).extras
