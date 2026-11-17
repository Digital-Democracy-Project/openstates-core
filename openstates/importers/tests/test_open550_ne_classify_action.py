"""OPEN-550: Nebraska's `classify_action` (openstates/ddp_states/ne.py), and the bill importer applying a state's
`classify_action` to every action it imports, so a relabelled state's re-scrape keeps the new labels.

The actions are real, in stored order, from the Mac replica (2026-11-17): Nebraska LB 376 (109th), cut to the lines
that matter plus their neighbours, with the labels the scraper's categorizer gives them.
"""

from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.cli.text_extract import relabel
from openstates.data.models import Bill, BillAction, Division, Jurisdiction, Organization
from openstates.ddp_states import state_rule
from openstates.ddp_states.ne import classify_action
from openstates.importers import BillImporter
from openstates.scrape import Bill as ScrapeBill

NE = "ocd-jurisdiction/country:us/state:ne/government"
AK = "ocd-jurisdiction/country:us/state:ak/government"

# (date, chamber, labels as scraped, text)
NE_LB376 = [
    ("2025-01-16", "legislature", ["introduction"], "Date of introduction"),
    ("2025-03-19", "legislature", [], "Cavanaugh, M. MO86 pending"),
    ("2025-03-19", "legislature", ["passage"], "Passed over"),
    ("2025-05-27", "legislature", ["reading-3"], "Placed on Final Reading"),
    ("2025-05-30", "legislature", [], "Dispensing of reading at large approved"),
    ("2025-05-30", "legislature", ["passage"], "Passed on Final Reading 47-1*-1"),
    ("2025-05-30", "legislature", ["passage"], "President/Speaker signed"),
    ("2025-05-30", "executive", ["executive-receipt"], "Presented to Governor on  May 30, 2025"),
    ("2025-06-06", "executive", ["executive-signature"], "Approved by Governor on June 4, 2025"),
]
# the same, as Nebraska's rule reads them: only the Final Reading vote is the bill's passage
NE_LB376_RELABELLED = [["introduction"], [], [], ["reading-3"], [], ["passage"], [], ["executive-receipt"], ["executive-signature"]]


def _rule(description, labels, chamber="legislature", date="2025-05-30"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        ("Passed on Final Reading 47-1*-1", ["passage"], ["passage"]),
        ("Passed on Final Reading with Emergency Clause 49-0-0", ["passage"], ["passage"]),
        ("President/Speaker signed", ["passage"], []),
        ("Passed over", ["passage"], []),
        # labels other than passage are never touched, nor is their order
        ("President/Speaker signed", ["reading-3", "passage", "filing"], ["reading-3", "filing"]),
        ("Placed on Final Reading", ["reading-3"], ["reading-3"]),
        # a failed final vote carries no passage and gets none
        ("Failed on Final Reading 31-17*-1", ["failure"], ["failure"]),
        ("Passed on Final Reading 47-1*-1", [], []),
    ],
)
def test_only_the_final_reading_vote_keeps_passage(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    out = _rule("Passed on Final Reading 47-1*-1", labels)
    assert out == ["passage"] and out is not labels
    _rule("President/Speaker signed", labels)
    assert labels == ["passage"]


@pytest.mark.parametrize("date,chamber,labels,description", NE_LB376)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_state_rule_finds_nebraska():
    assert state_rule("ne", "classify_action") is classify_action
    assert state_rule("ne", "classify_bill") is None


def _jurisdiction(jid, name):
    Division.objects.get_or_create(id="ocd-division/country:us", defaults={"name": "USA"})
    j = Jurisdiction.objects.create(id=jid, name=name, division_id="ocd-division/country:us")
    j.legislative_sessions.create(identifier="109", name="109", start_date="2025", end_date="2026")
    for classification in ("legislature", "executive"):
        Organization.objects.create(jurisdiction=j, name=classification, classification=classification)


def _scraped(actions, identifier="LB 376"):
    bill = ScrapeBill(identifier, "109", "A test bill", chamber="legislature")
    for date, chamber, labels, description in actions:
        bill.add_action(description, date, chamber=chamber, classification=list(labels))
    return bill.as_dict()


def _stored(identifier="LB 376"):
    return [list(a.classification) for a in BillAction.objects.filter(bill__identifier=identifier).order_by("order")]


@pytest.mark.django_db
def test_the_importer_applies_nebraskas_rule_to_every_action():
    _jurisdiction(NE, "Nebraska")

    BillImporter(NE).import_data([_scraped(NE_LB376)])

    assert _stored() == NE_LB376_RELABELLED
    # nothing else about the actions changes, and the bill's passage date is its Final Reading vote
    assert [a.description for a in BillAction.objects.order_by("order")] == [d for *_, d in NE_LB376]
    assert Bill.objects.get().latest_passage_date == "2025-05-30"


@pytest.mark.django_db
def test_a_rescrape_of_a_relabelled_bill_changes_nothing():
    _jurisdiction(NE, "Nebraska")
    BillImporter(NE).import_item(_scraped(NE_LB376))

    _, what = BillImporter(NE).import_item(_scraped(NE_LB376))

    assert what == "noop"
    assert _stored() == NE_LB376_RELABELLED


@pytest.mark.django_db
def test_a_state_without_a_module_imports_its_labels_unchanged():
    assert state_rule("ak", "classify_action") is None
    _jurisdiction(AK, "Alaska")

    importer = BillImporter(AK)
    importer.import_data([_scraped(NE_LB376)])

    assert importer.classify_action is None
    assert _stored() == [list(labels) for _, _, labels, _ in NE_LB376]


@pytest.mark.django_db
def test_a_jurisdiction_that_is_not_a_state_has_no_rule():
    assert BillImporter("jid").classify_action is None


@pytest.mark.django_db
def test_the_importer_passes_the_rule_what_relabel_passes():
    _jurisdiction(NE, "Nebraska")
    seen = []

    def spy(**kwargs):
        seen.append(kwargs)
        return list(kwargs["classification"])

    importer = BillImporter(NE)
    importer.classify_action = spy
    importer.import_data([_scraped(NE_LB376[-2:])])

    assert seen == [
        dict(
            description="Presented to Governor on  May 30, 2025",
            classification=["executive-receipt"],
            chamber="executive",
            date="2025-05-30",
        ),
        dict(
            description="Approved by Governor on June 4, 2025",
            classification=["executive-signature"],
            chamber="executive",
            date="2025-06-06",
        ),
    ]


def _relabel_dry_run(state):
    with mock.patch("openstates.cli.text_extract.init_django"):
        return CliRunner().invoke(relabel, [state, "--dry-run"])


@pytest.mark.django_db
def test_relabel_ne_now_runs_and_finds_the_stored_lines_the_rule_changes():
    bill = _bill(NE, "Nebraska", "LB 376", NE_LB376)
    before = _labels(bill)

    result = _relabel_dry_run("ne")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=2 unchanged=7" in result.output
    assert "action: passage -> (none): 2" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    # the importer and the relabel pass read an action the same way, so they agree
    _jurisdiction(NE, "Nebraska")
    BillImporter(NE).import_data([_scraped(NE_LB376)])

    result = _relabel_dry_run("ne")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=9" in result.output
