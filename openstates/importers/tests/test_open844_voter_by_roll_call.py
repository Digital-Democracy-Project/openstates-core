"""OPEN-844: a name several seated members fit is matched to the one left when the others are already matched on
the same roll call under their own names.

Real shape (production replica, 2026-11-24): Kentucky House 2026RS, three members of one surname seated. Each roll
call carrying the bare surname also carries it with an initial for two of them ("Lewis D", "Lewis S"), each already
matched by an other name, so the bare name can only be the third member.
"""
import pytest
from openstates.scrape import VoteEvent as ScrapeVoteEvent, Bill as ScrapeBill
from openstates.importers import VoteEventImporter, BillImporter
from openstates.data.models import Jurisdiction, Person, Organization, Division, VoteEvent

CURRENT = {"title": "Representative", "district": "1"}


@pytest.fixture
def house():
    Division.objects.create(id="ocd-division/country:us", name="USA")
    j = Jurisdiction.objects.create(id="jid", division_id="ocd-division/country:us")
    j.legislative_sessions.create(name="S", identifier="S", start_date="2026-01-06", end_date="2026-04-15")
    return Organization.objects.create(name="House", classification="lower", jurisdiction=j)


def member(house, name, family_name, other_name=None, current_role=CURRENT):
    person = Person.objects.create(name=name, family_name=family_name, current_role=current_role)
    person.memberships.create(organization=house, start_date="2025-01-01")
    if other_name:
        person.other_names.create(name=other_name)
    return person


def import_roll_calls(*roll_calls):
    """Each roll call is a list of voter names, all voting yes. Returns one {name: [voters]} per roll call."""
    bill = ScrapeBill("HB 1", "S", "A bill", chamber="lower")
    events = []
    for n, names in enumerate(roll_calls):
        ve = ScrapeVoteEvent(
            legislative_session="S",
            motion_text="Pass",
            identifier=f"RC{n}",
            start_date="2026-01-13",
            classification="passage",
            result="pass",
            bill="HB 1",
            bill_chamber="lower",
            chamber="lower",
        )
        for name in names:
            ve.yes(name)
        events.append(ve.as_dict())
    bi = BillImporter("jid")
    bi.import_data([bill.as_dict()])
    VoteEventImporter("jid", bi).import_data(events)
    result = []
    for n in range(len(roll_calls)):
        by_name = {}
        for pv in VoteEvent.objects.get(identifier=f"RC{n}").votes.all():
            by_name.setdefault(pv.voter_name, []).append(pv.voter)
        result.append(by_name)
    return result


@pytest.mark.django_db
def test_bare_surname_goes_to_the_one_member_not_named_on_the_roll_call(house):
    chris = member(house, "Chris Lewis", "Lewis")
    derek = member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    scott = member(house, "Scott Lewis", "Lewis", other_name="Lewis S")

    (rc,) = import_roll_calls(["Lewis", "Lewis D", "Lewis S"])

    assert rc == {"Lewis": [chris], "Lewis D": [derek], "Lewis S": [scott]}


@pytest.mark.django_db
def test_two_left_stays_unmatched(house):
    member(house, "Chris Lewis", "Lewis")
    member(house, "Anna Lewis", "Lewis")
    derek = member(house, "Derek Lewis", "Lewis", other_name="Lewis D")

    (rc,) = import_roll_calls(["Lewis", "Lewis D"])

    assert rc == {"Lewis": [None], "Lewis D": [derek]}


@pytest.mark.django_db
def test_only_the_same_roll_call_counts(house):
    member(house, "Chris Lewis", "Lewis")
    derek = member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    scott = member(house, "Scott Lewis", "Lewis", other_name="Lewis S")

    # Derek is named on the first roll call only; on the second, two candidates are left
    first, second = import_roll_calls(["Lewis D"], ["Lewis", "Lewis S"])

    assert first == {"Lewis D": [derek]}
    assert second == {"Lewis": [None], "Lewis S": [scott]}


@pytest.mark.django_db
def test_bare_surname_alone_stays_unmatched(house):
    member(house, "Chris Lewis", "Lewis")
    member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    member(house, "Scott Lewis", "Lewis", other_name="Lewis S")

    (rc,) = import_roll_calls(["Lewis"])

    assert rc == {"Lewis": [None]}


@pytest.mark.django_db
def test_a_matched_vote_is_never_changed(house):
    # only Derek holds a current role, so the existing tie-break already matches the bare name to him;
    # the rule must leave that alone even though he is also named on the roll call
    member(house, "Chris Lewis", "Lewis", current_role=None)
    derek = member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    member(house, "Scott Lewis", "Lewis", other_name="Lewis S", current_role=None)

    (rc,) = import_roll_calls(["Lewis", "Lewis D"])

    assert rc == {"Lewis": [derek], "Lewis D": [derek]}


@pytest.mark.django_db
def test_never_the_same_person_twice_on_one_roll_call(house):
    member(house, "Chris Lewis", "Lewis")
    member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    member(house, "Scott Lewis", "Lewis", other_name="Lewis S")

    # the bare name twice: each would be left with the same member, so neither is matched
    (rc,) = import_roll_calls(["Lewis", "Lewis", "Lewis D", "Lewis S"])

    assert rc["Lewis"] == [None, None]
    assert len({v for vs in rc.values() for v in vs if v}) == 2


@pytest.mark.django_db
def test_two_different_names_left_with_the_same_member_match_neither(house):
    member(house, "Chris Lewis", "Lewis")
    member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    member(house, "Scott Lewis", "Lewis", other_name="Lewis S")

    # two different spellings, each left with the same member once the others are dropped
    (rc,) = import_roll_calls(["Lewis", "LEWIS", "Lewis D", "Lewis S"])

    assert rc["Lewis"] == [None]
    assert rc["LEWIS"] == [None]


@pytest.mark.django_db
def test_a_member_not_seated_on_the_vote_day_is_not_a_candidate(house):
    chris = member(house, "Chris Lewis", "Lewis")
    derek = member(house, "Derek Lewis", "Lewis", other_name="Lewis D")
    scott = member(house, "Scott Lewis", "Lewis", other_name="Lewis S")
    # in the session, but gone before the roll call's day (2026-01-13)
    member(house, "Anna Lewis", "Lewis").memberships.update(end_date="2026-01-10")

    (rc,) = import_roll_calls(["Lewis", "Lewis D", "Lewis S"])

    assert rc == {"Lewis": [chris], "Lewis D": [derek], "Lewis S": [scott]}
