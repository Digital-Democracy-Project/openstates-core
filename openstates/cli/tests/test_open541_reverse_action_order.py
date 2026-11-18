"""OPEN-541: `os-text-extract reverse-action-order <state>` reverses a state's stored action order.

PLAN-state-specific-handling §6 Phase 2: a scraper fix stores a state's actions oldest first from then on, and
this pass reverses the history already stored, in a silent window. Since OPEN-544 it reads no per-state label: the
bills' own dates and the per-bill mark decide.

The bills are real ones from the Mac replica (2026-11-14), in their stored order: Louisiana HB 100 (2026),
Nebraska LB 100 (109th), North Carolina HB 100 (2025), all newest first, and US HR 10022 (118th), three actions
on one day.
"""

import csv
from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import ACTIONS_REVERSED_KEY, reverse_action_order
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import Bill, BillAction

LA = "ocd-jurisdiction/country:us/state:la/government"
NE = "ocd-jurisdiction/country:us/state:ne/government"
NC = "ocd-jurisdiction/country:us/state:nc/government"
US = "ocd-jurisdiction/country:us/government"

LA_HB100 = [
    ("2026-03-25", "Becomes HB 1009."),
    ("2026-03-24", "Reported by substitute (12-0)."),
    (
        "2026-03-09",
        "Read by title, under the rules, referred to the Committee on Administration of Criminal Justice.",
    ),
    ("2026-02-13", "First appeared in the Interim Calendar on 2/13/2026."),
    (
        "2026-02-09",
        "Under the rules, provisionally referred to the Committee on Administration of Criminal Justice.",
    ),
    ("2026-02-09", "Prefiled."),
]
NE_LB100 = [
    ("2026-04-17", "Indefinitely postponed"),
    ("2026-01-15", "Conrad name added"),
    ("2026-01-07", "Title printed. Carryover bill"),
    ("2025-01-17", "Notice of hearing for January 27, 2025"),
    ("2025-01-14", "Ballard name added"),
    ("2025-01-14", "Referred to Business and Labor Committee"),
    ("2025-01-10", "Date of introduction"),
]
NC_HB100 = [
    ("2025-03-12", "Re-ref Com On Finance"),
    ("2025-03-12", "Reptd Fav"),
    (
        "2025-02-12",
        "Ref to the Com on Housing and Development, if favorable, Finance, if favorable, Rules, Calendar, "
        "and Operations of the House",
    ),
    ("2025-02-12", "Passed 1st Reading"),
    ("2025-02-11", "Filed"),
]
US_HR10022 = [
    (
        "2024-10-22T04:00:00+00:00",
        "Referred to the Subcommittee on Highways and Transit.",
    ),
    (
        "2024-10-22T04:00:00+00:00",
        "Referred to the Committee on Transportation and Infrastructure, and in addition to the Committee on "
        "Education and the Workforce",
    ),
    ("2024-10-22T04:00:00+00:00", "Introduced in House"),
]


def _bill(jid, name, identifier, actions):
    bill = _make_bill(jid=jid, jurisdiction_name=name)
    bill.identifier = identifier
    bill.save()
    for order, (date, description) in enumerate(actions):
        bill.actions.create(
            organization=bill.from_organization,
            description=description,
            date=date,
            order=order,
        )
    return bill


def _stored(bill):
    return [
        (a.date, a.description)
        for a in BillAction.objects.filter(bill=bill).order_by("order")
    ]


def _orders(bill):
    return sorted(BillAction.objects.filter(bill=bill).values_list("order", flat=True))


def _fresh(bill):
    return Bill.objects.get(pk=bill.pk)


def _run(*args, jid=LA, state="la"):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=jid
    ):
        return CliRunner().invoke(reverse_action_order, [state, *args])


def _ok(*args, **kwargs):
    result = _run(*args, **kwargs)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def la_bill(db):
    return _bill(LA, "Louisiana", "HB 100", LA_HB100)


def _snapshot(bill):
    fresh = _fresh(bill)
    return (
        list(
            BillAction.objects.filter(bill=bill)
            .order_by("id")
            .values_list("id", "order")
        ),
        dict(fresh.extras),
        fresh.updated_at,
        fresh.latest_action_description,
    )


@pytest.mark.parametrize(
    "jid, name, state, identifier, actions, shape",
    [
        (LA, "Louisiana", "la", "HB 100", LA_HB100, "newest_first=1"),
        (NE, "Nebraska", "ne", "LB 100", NE_LB100, "newest_first=1"),
        (NC, "North Carolina", "nc", "HB 100", NC_HB100, "newest_first=1"),
        (US, "United States", "us", "HR 10022", US_HR10022, "same_day=1"),
    ],
)
def test_commit_stores_each_real_bill_reversed_with_the_same_count(
    db, tmp_path, jid, name, state, identifier, actions, shape
):
    bill = _bill(jid, name, identifier, actions)
    listing = tmp_path / "changes.csv"

    out = _ok("--commit", "--list", str(listing), jid=jid, state=state)
    assert not (tmp_path / "changes.csv.partial").exists()

    assert f"[COMMITTED] reversed=1 actions={len(actions)}" in out
    assert shape in out
    assert _stored(bill) == list(reversed(actions))
    assert _orders(bill) == list(range(len(actions)))
    assert _fresh(bill).extras[ACTIONS_REVERSED_KEY] is True
    # the change list names exactly the old and the new order
    (row,) = list(csv.DictReader(listing.open()))
    assert row["identifier"] == identifier and row["actions"] == str(len(actions))
    assert row["new_order"].split(";") == list(reversed(row["old_order"].split(";")))
    assert row["old_dates"].split(";") == [d for d, _ in actions]


def test_the_dry_run_counts_lists_and_writes_nothing(la_bill, tmp_path):
    before = _snapshot(la_bill)
    listing = tmp_path / "dry.csv"

    out = _ok("--dry-run", "--list", str(listing))

    assert "la: [DRY RUN] reversed=1 actions=6" in out
    assert _snapshot(la_bill) == before
    assert _stored(la_bill) == LA_HB100
    assert len(list(csv.DictReader(listing.open()))) == 1


@pytest.mark.parametrize(
    "jid, name, state, identifier, actions",
    [
        (LA, "Louisiana", "la", "HB 100", LA_HB100),
        # one day's actions: their dates can't tell a second run they were already reversed, the marker does
        (US, "United States", "us", "HR 10022", US_HR10022),
    ],
)
def test_a_second_run_changes_nothing(
    db, tmp_path, jid, name, state, identifier, actions
):
    bill = _bill(jid, name, identifier, actions)
    _ok(
        "--commit",
        "--list",
        str(tmp_path / "1.csv"),
        jid=jid,
        state=state,
    )
    after_first = _snapshot(bill)

    out = _ok(
        "--commit",
        "--list",
        str(tmp_path / "2.csv"),
        jid=jid,
        state=state,
    )

    assert "[COMMITTED] reversed=0 actions=0 skipped_already_reversed=1" in out
    assert _snapshot(bill) == after_first
    assert _stored(bill) == list(reversed(actions))


def test_a_bill_already_stored_oldest_first_or_with_one_action_is_left_alone(
    db, tmp_path
):
    rescraped = _bill(LA, "Louisiana", "HB 100", list(reversed(LA_HB100)))
    single = _bill(LA, "Louisiana", "HB 101", LA_HB100[-1:])

    out = _ok("--commit", "--list", str(tmp_path / "c.csv"))

    assert (
        "reversed=0 actions=0 skipped_already_oldest_first=1 skipped_single_action=1"
        in out
    )
    assert _stored(rescraped) == list(reversed(LA_HB100))
    assert ACTIONS_REVERSED_KEY not in _fresh(rescraped).extras
    assert ACTIONS_REVERSED_KEY not in _fresh(single).extras


@pytest.mark.parametrize(
    "manifest_text",
    [
        None,  # no manifest at all
        "la:\n  name: Louisiana\n",  # today's manifest: no actions block
        "la:\n  name: Louisiana\n  actions:\n    newest_first: false\n",  # the removed label, saying false
    ],
)
def test_it_reads_no_label_the_bills_own_dates_decide(
    la_bill, tmp_path, monkeypatch, manifest_text
):
    # OPEN-544: the manifest's actions.newest_first is gone, and the command no longer refuses a state for lack of it.
    # Whatever a jurisdictions.yaml in reach says, a newest-first bill is reversed and its mark set.
    monkeypatch.chdir(tmp_path)
    if manifest_text is not None:
        (tmp_path / "jurisdictions.yaml").write_text(manifest_text)
        monkeypatch.setenv(
            "DDP_JURISDICTIONS_MANIFEST", str(tmp_path / "jurisdictions.yaml")
        )
    else:
        monkeypatch.delenv("DDP_JURISDICTIONS_MANIFEST", raising=False)

    out = _ok("--commit", "--list", str(tmp_path / "c.csv"))

    assert "[COMMITTED] reversed=1 actions=6" in out
    assert _stored(la_bill) == list(reversed(LA_HB100))
    assert _fresh(la_bill).extras[ACTIONS_REVERSED_KEY] is True


@pytest.mark.parametrize(
    "option",
    [["--manifest", "jurisdictions.yaml"], ["--even-if-manifest-says-oldest-first"]],
)
def test_the_removed_label_options_are_gone(la_bill, tmp_path, option):
    result = _run("--dry-run", *option)

    assert result.exit_code == 2 and "No such option" in result.output
    assert _stored(la_bill) == LA_HB100


def test_no_core_code_reads_the_removed_label():
    # OPEN-544 acceptance: no code reads the manifest's actions.newest_first. "newest_first" stays only as the name of
    # a date shape (`_date_direction`); any read of it as a key or a manifest path fails here.
    import pathlib
    import re

    import openstates
    from openstates.cli import text_extract

    assert not hasattr(text_extract, "_manifest_newest_first")
    read = re.compile(
        r"""(get\(\s*|\[\s*|manifest_value\([^)]*)["'](actions\.)?newest_first["']"""
    )
    root = pathlib.Path(openstates.__file__).parent
    readers = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        if "tests" not in path.relative_to(root).parts
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if read.search(line)
    ]
    assert readers == []


def test_commit_needs_a_change_list_file(la_bill):
    result = _run("--commit")

    assert result.exit_code != 0 and "--commit needs --list FILE" in result.output
    assert _stored(la_bill) == LA_HB100


def test_the_latest_action_is_recomputed_from_the_new_order(db, tmp_path):
    # NC HB 100's two newest actions share a day; the stored latest is whichever comes last in order
    bill = _bill(NC, "North Carolina", "HB 100", NC_HB100)
    from openstates.importers.computed_fields import update_bill_fields

    update_bill_fields(bill, save=True)
    assert _fresh(bill).latest_action_description == "Reptd Fav"

    out = _ok(
        "--commit",
        "--list",
        str(tmp_path / "c.csv"),
        jid=NC,
        state="nc",
    )

    assert "latest action fields recomputed and changed: 1" in out
    assert _fresh(bill).latest_action_description == "Re-ref Com On Finance"
    assert _fresh(bill).first_action_date == "2025-02-11"


def test_undo_puts_the_old_order_back(la_bill, tmp_path):
    la_bill.extras = {"keep": "me"}
    la_bill.save()
    _ok("--commit", "--list", str(tmp_path / "c.csv"))

    # the undo is the rollback inside the silent window
    assert "[DRY RUN] restored=1 actions=6" in _ok("--undo")
    assert _stored(la_bill) == list(reversed(LA_HB100))

    out = _ok("--undo", "--commit", "--list", str(tmp_path / "u.csv"))

    assert "[COMMITTED] restored=1 actions=6" in out
    assert _stored(la_bill) == LA_HB100
    assert _fresh(la_bill).extras == {"keep": "me"}


def test_expect_aborts_before_any_write_when_the_count_moved(la_bill, tmp_path):
    result = _run(
        "--commit",
        "--list",
        str(tmp_path / "c.csv"),
        "--expect",
        "2",
    )

    assert result.exit_code != 0
    assert "1 bills would change, not the expected 2; nothing written" in result.output
    assert _stored(la_bill) == LA_HB100
    # no change list is left behind that reads as a committed run
    assert list(tmp_path.glob("c.csv*")) == []


def test_another_state_is_never_touched(la_bill, tmp_path):
    ne = _bill(NE, "Nebraska", "LB 100", NE_LB100)

    _ok("--commit", "--list", str(tmp_path / "c.csv"))

    assert _stored(ne) == NE_LB100
    assert ACTIONS_REVERSED_KEY not in _fresh(ne).extras


@pytest.mark.parametrize("drift", ["actions", "extras"])
def test_a_bill_that_changes_mid_run_aborts_the_whole_run(db, tmp_path, drift):
    first, last = sorted(
        [
            _bill(LA, "Louisiana", "HB 100", LA_HB100),
            _bill(LA, "Louisiana", "HB 200", LA_HB100),
        ],
        key=lambda b: b.pk,
    )
    real = BillAction.objects.bulk_update
    named_early = []

    def bulk_update_then_drift(objs, fields, *args, **kwargs):
        done = real(objs, fields, *args, **kwargs)
        # mid-transaction, the change list has not taken its final name yet
        named_early.append((tmp_path / "c.csv").exists())
        if objs[0].bill_id == first.pk:
            # an import lands on the second bill after it was read, before it is written
            if drift == "actions":
                last.actions.create(
                    organization=last.from_organization,
                    description="Signed",
                    date="2026-06-01",
                    order=6,
                )
            else:
                Bill.objects.filter(pk=last.pk).update(extras={"from": "an import"})
        return done

    with mock.patch.object(
        BillAction.objects, "bulk_update", side_effect=bulk_update_then_drift
    ):
        result = _run("--commit", "--list", str(tmp_path / "c.csv"))

    assert result.exit_code != 0
    assert "changed since it was read; rolled back, nothing written" in result.output
    # the first bill's write is rolled back, not merely never attempted
    assert _stored(first) == LA_HB100
    assert ACTIONS_REVERSED_KEY not in _fresh(first).extras
    assert list(tmp_path.glob("c.csv*")) == []
    assert named_early == [False]


def test_a_change_list_that_cannot_take_its_name_says_the_commit_is_done(
    la_bill, tmp_path
):
    with mock.patch(
        "openstates.cli.text_extract.os.replace", side_effect=OSError("disk full")
    ):
        result = _run("--commit", "--list", str(tmp_path / "c.csv"))

    assert result.exit_code != 0
    assert "COMMITTED, but the change list could not take its name" in result.output
    assert "Do not run again" in result.output
    # the record is complete and kept under its temporary name; the change itself stands
    assert len(list(csv.DictReader((tmp_path / "c.csv.partial").open()))) == 1
    assert _stored(la_bill) == list(reversed(LA_HB100))
