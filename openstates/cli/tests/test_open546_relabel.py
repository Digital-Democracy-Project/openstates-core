"""OPEN-546: `os-text-extract relabel <state>` re-applies a state's current label rules to its stored history.

PLAN-state-specific-handling §6 Phase 3: a state's action rule moves from the broker into its `classify_action`
(`openstates/ddp_states/<state>.py`); the bill importer applies it to what it re-imports (OPEN-550), and this pass to what is stored.

These tests stand in their own rules (OPEN-545, 547-552 add the real modules; Nebraska's and Massachusetts' are
tested in openstates/importers/tests/test_open55*), so the rules here are the broker's own readings of two
states, written as `classify_action` / `classify_bill` (ddp-broker-py `bill_stage._read_michigan`, `_read_virginia`,
`bill_events.read_classification`). The actions are real, in stored order, from the Mac replica (2026-11-16):
Michigan HB 4001 (2025-2026) and Virginia HB 625 (2026), each cut to the lines that matter plus their neighbours.
"""

import csv
import re
from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import relabel, relabel_non_bills
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import Bill, BillAction, Organization
from openstates.ddp_states import state_rule

MI = "ocd-jurisdiction/country:us/state:mi/government"
VA = "ocd-jurisdiction/country:us/state:va/government"

MI_HB4001 = [
    ("2025-01-23", "lower", ["reading-3"], "read a third time"),
    (
        "2025-01-23",
        "lower",
        ["passage"],
        "passed; given immediate effect Roll Call #4 Yeas 63 Nays 41 Excused 0 Not Voting 6",
    ),
    ("2025-01-23", "lower", [], "transmitted"),
    ("2025-02-04", "upper", ["passage"], "PASSED BY HOUSE WITH IMMEDIATE EFFECT"),
    ("2025-02-04", "upper", ["referral-committee"], "REFERRED TO COMMITTEE ON REGULATORY AFFAIRS"),
]
VA_HB625 = [
    ("2026-01-20", "lower", ["referral-committee"], "Assigned HCL sub: Subcommittee #1"),
    (
        "2026-02-03",
        "lower",
        ["committee-failure"],
        "Subcommittee failed to recommend reporting (4-Y 5-N)",
    ),
    ("2026-02-03", "lower", [], "Reconsidered by Labor and Commerce (Voice Vote)"),
    (
        "2026-02-05",
        "lower",
        ["committee-passage"],
        "Reported from Labor and Commerce with amendment(s) (15-Y 7-N)",
    ),
    (
        "2026-02-11",
        "lower",
        ["passage", "reading-3"],
        "Read third time and passed House (63-Y 35-N 0-A)",
    ),
    (
        "2026-04-13",
        "executive",
        ["executive-signature"],
        "Approved by Governor-Chapter 641 (effective 7/1/2026)",
    ),
]

_MI_CHAMBER_MESSAGE = re.compile(r"^PASSED BY (HOUSE|SENATE)\b", re.I)
_VA_SUBCOMMITTEE_FAILED = re.compile(r"^Subcommittee failed to recommend\b", re.I)
_VA_TYPE_BY_PREFIX = {
    "HR": "resolution",
    "SR": "resolution",
    "HJ": "joint resolution",
    "SJ": "joint resolution",
}


def mi_classify_action(*, description, classification, chamber, date):
    # the receiving chamber's notice that the other passed it is not its own passage
    if _MI_CHAMBER_MESSAGE.match(description or ""):
        return [c for c in classification if c != "passage"]
    return classification


def va_classify_action(*, description, classification, chamber, date):
    # a subcommittee only recommends; the committee's own vote is the failure
    if _VA_SUBCOMMITTEE_FAILED.match(description or ""):
        return [c for c in classification if c != "committee-failure"]
    return classification


def va_classify_bill(*, identifier, title, classification):
    read = _VA_TYPE_BY_PREFIX.get("".join((identifier or "").split())[:2].upper())
    if read is None:
        return classification
    return [read if c == "bill" else c for c in classification]


RULES = {
    "mi": {"classify_action": mi_classify_action},
    "va": {"classify_action": va_classify_action, "classify_bill": va_classify_bill},
}


def _bill(jid, name, identifier, actions, classification=("bill",)):
    bill = _make_bill(jid=jid, jurisdiction_name=name)
    bill.identifier = identifier
    bill.classification = list(classification)
    bill.save()
    for order, (date, chamber, labels, description) in enumerate(actions):
        org, _ = Organization.objects.get_or_create(
            jurisdiction_id=jid, classification=chamber, defaults={"name": chamber}
        )
        bill.actions.create(
            organization=org,
            description=description,
            date=date,
            classification=labels,
            order=order,
        )
    bill.latest_passage_date = max(
        (d for d, _, labels, _ in actions if "passage" in labels), default=None
    )
    bill.save()
    return bill


def _actions(bill):
    """Everything stored about a bill's actions, labels apart: what a re-label must never change."""
    return [
        (a.id, a.order, a.date, a.description, a.organization_id)
        for a in BillAction.objects.filter(bill=bill).order_by("order")
    ]


def _labels(bill):
    return [
        list(a.classification)
        for a in BillAction.objects.filter(bill=bill).order_by("order")
    ]


def _fresh(bill):
    return Bill.objects.get(pk=bill.pk)


def _rule(rules):
    return lambda state, name: rules.get(state, {}).get(name)


def _run(*args, command=relabel, jid=MI, state="mi", rules=RULES):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=jid
    ), mock.patch("openstates.ddp_states.state_rule", _rule(rules)):
        return CliRunner().invoke(command, [state, *args])


def _ok(*args, **kwargs):
    result = _run(*args, **kwargs)
    assert result.exit_code == 0, result.output
    return result.output


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def mi(db):
    return _bill(MI, "Michigan", "HB 4001", MI_HB4001)


@pytest.fixture
def va(db):
    return dict(
        hb625=_bill(VA, "Virginia", "HB 625", VA_HB625),
        hr1=_bill(VA, "Virginia", "HR 1", [], classification=("bill",)),
        hj2=_bill(VA, "Virginia", "HJ 2", [], classification=("bill",)),
        ca=_bill(VA, "Virginia", "SB 3", [], classification=("constitutional amendment",)),
    )


def test_the_dry_run_lists_every_change_with_old_and_new_labels_and_writes_nothing(mi, tmp_path):
    before = (_actions(mi), _labels(mi), _fresh(mi).updated_at)
    listing = tmp_path / "dry.csv"

    out = _ok("--dry-run", "--list", str(listing))

    assert "[DRY RUN] actions changed=1 unchanged=4" in out
    assert "action: passage -> (none): 1" in out
    assert (_actions(mi), _labels(mi), _fresh(mi).updated_at) == before
    [row] = _rows(listing)
    assert (row["kind"], row["identifier"], row["order"], row["chamber"]) == (
        "action",
        "HB 4001",
        "3",
        "upper",
    )
    assert (row["text"], row["old"], row["new"]) == (
        "PASSED BY HOUSE WITH IMMEDIATE EFFECT",
        "passage",
        "",
    )


def test_the_commit_changes_only_labels_and_writes_the_list_it_applied(mi, tmp_path):
    before = _actions(mi)
    stamp = _fresh(mi).updated_at
    _ok("--list", str(tmp_path / "dry.csv"))
    listing = tmp_path / "committed.csv"

    out = _ok("--commit", "--list", str(listing))

    assert "[COMMITTED] actions changed=1 unchanged=4" in out
    assert _actions(mi) == before  # same rows, order, dates, text and chamber
    assert _labels(mi) == [labels for _, _, labels, _ in MI_HB4001[:3]] + [
        [],
        ["referral-committee"],
    ]
    # the committed list is the dry run's, and no half-written list is left behind
    assert _rows(listing) == _rows(tmp_path / "dry.csv")
    assert not (tmp_path / "committed.csv.partial").exists()
    # the House's own vote is now the latest passage, and the change is visible to updated_since readers
    fresh = _fresh(mi)
    assert fresh.latest_passage_date == "2025-01-23"
    assert fresh.updated_at > stamp
    assert "bills whose computed fields changed: " in out and "latest_passage_date=1" in out


def test_running_twice_changes_nothing_the_second_time(mi, va, tmp_path):
    _ok("--commit", "--list", str(tmp_path / "1.csv"))
    _ok("--commit", "--list", str(tmp_path / "2.csv"), jid=VA, state="va")
    labels = (_labels(mi), _labels(va["hb625"]))

    assert "actions changed=0 unchanged=5" in _ok("--commit", "--list", str(tmp_path / "3.csv"))
    out = _ok("--commit", "--list", str(tmp_path / "4.csv"), jid=VA, state="va")
    assert "actions changed=0 unchanged=6 | bills changed=0 unchanged=4" in out
    assert (_labels(mi), _labels(va["hb625"])) == labels
    assert _rows(tmp_path / "3.csv") == [] and _rows(tmp_path / "4.csv") == []


def test_a_second_state_relabels_actions_and_bill_labels(va, tmp_path):
    stamps = {k: _fresh(b).updated_at for k, b in va.items()}
    before = _actions(va["hb625"])

    out = _ok("--commit", "--list", str(tmp_path / "va.csv"), jid=VA, state="va")

    assert "actions changed=1 unchanged=5 | bills changed=2 unchanged=2" in out
    assert "action: committee-failure -> (none): 1" in out
    assert "bill: bill -> joint resolution: 1" in out and "bill: bill -> resolution: 1" in out
    assert _actions(va["hb625"]) == before
    assert _labels(va["hb625"])[1] == []
    assert [_fresh(va[k]).classification for k in ("hb625", "hr1", "hj2", "ca")] == [
        ["bill"],
        ["resolution"],
        ["joint resolution"],
        ["constitutional amendment"],
    ]
    # a bill-only label change does not move updated_at (ddp-sync's copy waits for the bill's next real update; see the
    # command's docstring); an action change does
    assert _fresh(va["hr1"]).updated_at == stamps["hr1"]
    assert _fresh(va["hb625"]).updated_at > stamps["hb625"]
    kinds = sorted((r["kind"], r["identifier"], r["old"], r["new"]) for r in _rows(tmp_path / "va.csv"))
    assert kinds == [
        ("action", "HB 625", "committee-failure", ""),
        ("bill", "HJ 2", "bill", "joint resolution"),
        ("bill", "HR 1", "bill", "resolution"),
    ]


def test_undo_from_the_committed_list_puts_the_old_labels_back(mi, va, tmp_path):
    labels = (_labels(mi), _labels(va["hb625"]), _fresh(va["hr1"]).classification)
    _ok("--commit", "--list", str(tmp_path / "mi.csv"))
    _ok("--commit", "--list", str(tmp_path / "va.csv"), jid=VA, state="va")

    # a list of one state is refused for another
    wrong = _run("--undo", str(tmp_path / "va.csv"), "--commit", "--list", str(tmp_path / "x.csv"))
    assert wrong.exit_code != 0 and "not a bill of this state" in wrong.output

    out = _ok("--undo", str(tmp_path / "mi.csv"), "--commit", "--list", str(tmp_path / "mi-undo.csv"))
    assert "[COMMITTED] actions restored=1 | bills restored=0" in out
    _ok("--undo", str(tmp_path / "va.csv"), "--commit", "--list", str(tmp_path / "va-undo.csv"), jid=VA, state="va")

    assert (_labels(mi), _labels(va["hb625"]), _fresh(va["hr1"]).classification) == labels
    assert _fresh(mi).latest_passage_date == "2025-02-04"
    assert {(r["old"], r["new"]) for r in _rows(tmp_path / "mi-undo.csv")} == {("", "passage")}


def test_expect_aborts_before_any_write_and_leaves_no_list(mi, tmp_path):
    listing = tmp_path / "list.csv"
    result = _run("--commit", "--expect", "2", "--list", str(listing))

    assert result.exit_code != 0
    assert "1 items would change, not the expected 2; nothing written" in result.output
    assert "passage" in _labels(mi)[3]
    assert not listing.exists() and not (tmp_path / "list.csv.partial").exists()
    assert "[COMMITTED]" in _ok("--commit", "--expect", "1", "--list", str(listing))


def test_commit_needs_a_new_list_file(mi, tmp_path):
    assert "--commit needs --list" in _run("--commit").output
    existing = tmp_path / "old.csv"
    existing.write_text("kept")
    result = _run("--commit", "--list", str(existing))
    assert result.exit_code != 0 and "never overwritten" in result.output
    assert existing.read_text() == "kept"
    assert "passage" in _labels(mi)[3]


def test_a_state_without_rules_is_refused(mi):
    result = _run("--dry-run", rules={})
    assert result.exit_code != 0
    assert "has no classify_action or classify_bill" in result.output


def test_a_rule_that_would_move_again_on_its_own_output_is_refused(mi, tmp_path):
    def flips(*, description, classification, chamber, date):
        return [] if "passage" in classification else ["passage"]

    result = _run("--commit", "--list", str(tmp_path / "l.csv"), rules={"mi": {"classify_action": flips}})

    assert result.exit_code != 0
    assert "must give the same answer on its own output" in result.output
    assert _labels(mi) == [labels for _, _, labels, _ in MI_HB4001]


def test_another_state_is_never_touched(mi, va, tmp_path):
    va_labels = _labels(va["hb625"])
    _ok("--commit", "--list", str(tmp_path / "mi.csv"))
    assert _labels(va["hb625"]) == va_labels
    assert _fresh(va["hr1"]).classification == ["bill"]


def test_an_action_that_changes_mid_run_aborts_the_whole_run(va, tmp_path):
    # Bills are written before actions, so HR 1's and HJ 2's new labels are written first; HB 625's action drifts
    # after it was read, so those writes must be rolled back, not merely never attempted.
    action = BillAction.objects.get(bill=va["hb625"], description__startswith="Subcommittee")

    def classify_then_drift(*, description, classification, chamber, date):
        if description.startswith("Subcommittee"):
            # an import lands after the row was read, before it is written
            BillAction.objects.filter(pk=action.pk).update(classification=["committee-failure", "failure"])
        return va_classify_action(
            description=description, classification=classification, chamber=chamber, date=date
        )

    rules = {"va": {"classify_action": classify_then_drift, "classify_bill": va_classify_bill}}
    result = _run("--commit", "--list", str(tmp_path / "l.csv"), jid=VA, state="va", rules=rules)

    assert result.exit_code != 0
    assert "of HB 625 changed since it was read; rolled back, nothing written" in result.output
    assert _fresh(va["hr1"]).classification == ["bill"]
    assert _fresh(va["hj2"]).classification == ["bill"]
    assert not (tmp_path / "l.csv").exists() and not (tmp_path / "l.csv.partial").exists()


def test_an_action_whose_text_changes_mid_run_is_not_written_with_its_old_reading(mi, tmp_path):
    # the label is unchanged but the text it was read from is not: the new label would be the old text's
    action = BillAction.objects.get(bill=mi, description__startswith="PASSED BY")

    def classify_then_edit(*, description, classification, chamber, date):
        if description.startswith("PASSED BY"):
            BillAction.objects.filter(pk=action.pk).update(description="passed; given immediate effect")
        return mi_classify_action(
            description=description, classification=classification, chamber=chamber, date=date
        )

    rules = {"mi": {"classify_action": classify_then_edit}}
    result = _run("--commit", "--list", str(tmp_path / "l.csv"), rules=rules)

    assert result.exit_code != 0
    assert "changed since it was read; rolled back, nothing written" in result.output
    assert BillAction.objects.get(pk=action.pk).classification == ["passage"]


def test_undo_refuses_a_list_that_is_not_a_relabel_record(mi, va, tmp_path):
    _ok("--commit", "--list", str(tmp_path / "mi.csv"))
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    result = _run("--undo", str(empty))
    assert result.exit_code != 0 and "is not a `relabel` change list" in result.output

    # a row naming this state's bill but another bill's action id writes nothing: the action is not on that bill
    [row] = _rows(tmp_path / "mi.csv")
    va_action = BillAction.objects.filter(bill=va["hb625"]).order_by("order").first()
    BillAction.objects.filter(pk=va_action.pk).update(classification=[])
    forged = tmp_path / "forged.csv"
    with open(forged, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(dict(row, id=str(va_action.pk)))
    result = _run("--undo", str(forged), "--commit", "--list", str(tmp_path / "u.csv"))
    assert result.exit_code != 0 and "changed since it was read" in result.output
    assert BillAction.objects.get(pk=va_action.pk).classification == []


def test_state_rule_finds_nothing_for_a_state_without_a_module():
    assert state_rule("zz", "classify_action") is None


def test_relabel_non_bills_still_runs_on_the_shared_code(db):
    # its own tests (test_open517_ma_relabel_non_bills) cover the behaviour; this checks the CLI is still wired
    result = _run("--dry-run", command=relabel_non_bills, jid=MI)
    assert result.exit_code == 0, result.output
    assert "[DRY RUN] relabelled=0" in result.output


# OPEN-615 (review follow-up 2): every computed field a relabel changes is counted, not only latest_passage_date.

FIELDS = (
    "first_action_date",
    "latest_action_date",
    "latest_action_description",
    "latest_passage_date",
)


def _fields(bill):
    fresh = _fresh(bill)
    return {f: getattr(fresh, f) for f in FIELDS}


def _settled(bill, **stale):
    """The bill's computed fields as a clean import leaves them, then any field overwritten with a stale value."""
    from openstates.importers.computed_fields import update_bill_fields

    update_bill_fields(bill)
    Bill.objects.filter(pk=bill.pk).update(**stale)
    return bill


def _counts(out, word):
    line = next(x for x in out.splitlines() if f"bills whose computed fields {word}:" in x)
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}


def _changed(before, after):
    """Per field, 1 if the stored value moved: the independent answer the printed counts must match."""
    return {f: int(before[f] != after[f]) for f in FIELDS}


def test_the_dry_run_and_the_commit_count_each_computed_field_that_really_changes(mi, tmp_path):
    _settled(mi)
    before = _fields(mi)

    dry = _counts(_ok("--dry-run"), "would change")
    assert _fields(mi) == before  # the preview wrote nothing

    committed = _counts(_ok("--commit", "--list", str(tmp_path / "c.csv")), "changed")
    after = _fields(mi)

    assert dry == committed == _changed(before, after)
    # only the passage date moves: the receiving chamber's notice was the latest "passage"
    assert committed == dict(dict.fromkeys(FIELDS, 0), latest_passage_date=1)
    assert after["latest_passage_date"] == "2025-01-23"


def test_a_field_an_older_import_left_stale_is_counted_too(mi, tmp_path):
    _settled(
        mi,
        latest_action_description="stale",
        first_action_date="2024-12-31",
        latest_action_date="2024-12-31",
    )
    before = _fields(mi)

    dry = _counts(_ok("--dry-run"), "would change")
    committed = _counts(_ok("--commit", "--list", str(tmp_path / "c.csv")), "changed")
    after = _fields(mi)

    assert dry == committed == _changed(before, after)
    assert committed == dict.fromkeys(FIELDS, 1)
    assert (after["first_action_date"], after["latest_action_date"], after["latest_action_description"]) == (
        "2025-01-23",
        "2025-02-04",
        "REFERRED TO COMMITTEE ON REGULATORY AFFAIRS",
    )


def test_a_bill_label_change_alone_changes_no_computed_field(va, tmp_path):
    for bill in va.values():
        _settled(bill)

    out = _ok("--commit", "--list", str(tmp_path / "va.csv"), jid=VA, state="va")

    # HB 625's subcommittee line was never its passage, so nothing computed moves; HR 1 / HJ 2 have no actions
    assert _counts(out, "changed") == dict.fromkeys(FIELDS, 0)
