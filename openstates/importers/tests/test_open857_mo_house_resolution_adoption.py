"""OPEN-857: the House's adoption of a Missouri resolution is its House passage (openstates/ddp_states/mo.py
`classify_action`).

Every text is real, from the Mac replica (openstates_rds_repl_20260911, 2026-11-24, session 2026), with the labels it is
stored with: house.mo.gov's "Adopted (H)" (HR 1, HR 2, HR 3872, HR 3873, HCR 21, HCR 22) and "Adopted (H) - AYES: 31
NOES: 0 PRESENT: 0" (HCR 21, HCR 22), and senate.mo.gov's bare "H adopted" (SCR 21). HR 3872 is cut to its whole
stored history; HCR 21 to its name-free lines (the escort committee lines name members).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states.mo import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

MO = "ocd-jurisdiction/country:us/state:mo/government"
SESSION = "2026"

# (date, chamber, labels as stored, text)
MO_HR3872 = [
    ("2026-01-07", "lower", ["introduction"], "Offered (H)"),
    ("2026-01-07", "lower", [], "Adopted (H)"),
    ("2026-01-07", "lower", [], "House Message (H)"),
]
MO_HR3872_RELABELLED = [["introduction"], ["passage"], []]

MO_HCR21 = [
    ("2026-01-07", "lower", ["introduction"], "Offered (H)"),
    ("2026-01-07", "lower", [], "Adopted (H)"),
    ("2026-01-07", "lower", [], "Reported to the Senate"),
    ("2026-01-08", "lower", [], "Taken Up"),
    ("2026-01-08", "lower", [], "Adopted (H) - AYES: 31 NOES: 0 PRESENT: 0"),
    ("2026-01-08", "upper", [], "Senate Message (S)"),
]
MO_HCR21_RELABELLED = [["introduction"], ["passage"], [], [], ["passage"], []]


def _rule(description, labels, chamber="lower", date="2026-01-07"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


# every stored form (Mac replica, 2026-11-24), all stored with no label
@pytest.mark.parametrize(
    "description",
    ["Adopted (H)", "Adopted (H) - AYES: 31 NOES: 0 PRESENT: 0", "H adopted"],
)
def test_the_houses_adoption_of_a_resolution_is_passage(description):
    assert _rule(description, []) == ["passage"]


# the House's adoption of a substitute or an amendment, and its other lines, are left as they are
@pytest.mark.parametrize(
    "description,labels",
    [
        ("HCS Adopted (H)", []),
        ("SS Adopted (S) - AYES: 22 NOES: 10 PRESENT: 0", []),
        ("Perfected with Amendments (H) - HA 1, adopted", []),
        ("House Adopts (H) - SS SCS", []),
        ("HCS, as amended, H adopted --(4981H.05C)", []),
        ("H adopts CCR --(4981S06.1SR)", []),
        ("Offered (H)", ["introduction"]),
        ("House Message (H)", []),
        ("Reported Do Pass (H) - AYES: 6 NOES: 0 PRESENT: 0", ["committee-passage"]),
    ],
)
def test_the_houses_other_lines_are_unchanged(description, labels):
    assert _rule(description, labels) == labels


def test_passage_is_added_once_and_other_labels_keep_their_order():
    assert _rule("Adopted (H)", ["passage"]) == ["passage"]
    assert _rule("Adopted (H)", ["receipt"]) == ["receipt", "passage"]
    labels = []
    assert _rule("H adopted", labels) == ["passage"] and labels == []


@pytest.mark.parametrize("date,chamber,labels,description", MO_HR3872 + MO_HCR21)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def _mo():
    _jurisdiction(MO, "Missouri", session=SESSION, chambers=("lower", "upper"))


@pytest.mark.django_db
def test_the_importer_stores_the_house_resolutions_passage():
    _mo()

    BillImporter(MO).import_item(_scraped(MO_HR3872, "HR 3872", session=SESSION, chamber="lower"))
    BillImporter(MO).import_item(_scraped(MO_HCR21, "HCR 21", session=SESSION, chamber="lower"))

    assert _stored("HR 3872") == MO_HR3872_RELABELLED
    assert _stored("HCR 21") == MO_HCR21_RELABELLED
    assert Bill.objects.get(identifier="HR 3872").latest_passage_date == "2026-01-07"
    assert Bill.objects.get(identifier="HCR 21").latest_passage_date == "2026-01-08"


@pytest.mark.django_db
def test_relabel_mo_finds_the_stored_adoptions():
    hr = _bill(MO, "Missouri", "HR 3872", MO_HR3872)
    hcr = _bill(MO, "Missouri", "HCR 21", MO_HCR21)
    before = (_labels(hr), _labels(hcr))

    result = _relabel_dry_run("mo")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=6" in result.output
    assert "action: (none) -> passage: 3" in result.output
    assert (_labels(hr), _labels(hcr)) == before
