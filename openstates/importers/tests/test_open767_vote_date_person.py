"""OPEN-767: a vote's voter is matched on the vote's own date when the session-wide match finds two people.

Real shapes (production replica, 2026-11-22):
- Nebraska, session 109 (2025-01-08 to 2026-06-01, one `legislature` body): Glen Meyer from 2025-01-08; Fred Meyer
  until 2025-01-08 and again from 2026-01-14. Bare "Meyer" (955 votes, 2025-01-29 to 2026-01-14) is Glen's.
- Louisiana House, 2026 regular session (2026-03-09 to 2026-06-01): Chance Henry all session; Dana Henry seated
  2026-03-24. Bare "Henry" (37 votes, 2026-03-17 to 03-23) is Chance's.
- Indiana-style dated other names: "Mr. Speaker" belongs to one member until a date and another after it.
"""
import pytest
from openstates.scrape import VoteEvent as ScrapeVoteEvent, Bill as ScrapeBill
from openstates.importers import VoteEventImporter, BillImporter
from openstates.data.models import Jurisdiction, Person, Organization, Division, VoteEvent

CURRENT = {"title": "Senator", "district": "1"}


def make_state(session_start, session_end, chamber):
    Division.objects.create(id="ocd-division/country:us", name="USA")
    j = Jurisdiction.objects.create(id="jid", division_id="ocd-division/country:us")
    j.legislative_sessions.create(name="S", identifier="S", start_date=session_start, end_date=session_end)
    return Organization.objects.create(name=chamber, classification=chamber, jurisdiction=j)


def import_votes(chamber, voter_name, dates):
    """One roll call per date, each with `voter_name` voting yes. Returns {date: voter}."""
    bill = ScrapeBill("LB 1", "S", "A bill", chamber=chamber)
    events = []
    for n, day in enumerate(dates):
        ve = ScrapeVoteEvent(
            legislative_session="S",
            motion_text=f"Roll call {n}",
            identifier=f"RC{n}",
            start_date=day,
            classification="passage",
            result="pass",
            bill="LB 1",
            bill_chamber=chamber,
            chamber=chamber,
        )
        ve.yes(voter_name)
        events.append(ve.as_dict())
    bi = BillImporter("jid")
    bi.import_data([bill.as_dict()])
    VoteEventImporter("jid", bi).import_data(events)
    return {ve.start_date: ve.votes.get().voter for ve in VoteEvent.objects.all()}


@pytest.mark.django_db
def test_nebraska_meyer_goes_to_the_meyer_serving_on_the_vote_date():
    leg = make_state("2025-01-08", "2026-06-01", "legislature")
    glen = Person.objects.create(name="Glen Meyer", family_name="Meyer", current_role=CURRENT)
    fred = Person.objects.create(name="Fred Meyer", family_name="Meyer", current_role=CURRENT)
    glen.memberships.create(organization=leg, start_date="2025-01-08")
    fred.memberships.create(organization=leg, start_date="2021-01-06", end_date="2025-01-08")
    fred.memberships.create(organization=leg, start_date="2026-01-14")

    voters = import_votes("legislature", "Meyer", ["2025-01-29", "2025-06-02", "2026-01-14"])

    assert voters["2025-01-29"] == glen
    assert voters["2025-06-02"] == glen
    # Fred's new term starts that day: the name fits both, so it stays blank rather than guessed
    assert voters["2026-01-14"] is None


@pytest.mark.django_db
def test_louisiana_henry_goes_to_chance_before_dana_is_seated():
    house = make_state("2026-03-09", "2026-06-01", "lower")
    chance = Person.objects.create(name="Chance Henry", family_name="Henry", current_role=CURRENT)
    dana = Person.objects.create(name="Dana Henry", family_name="Henry", current_role=CURRENT)
    chance.memberships.create(organization=house, start_date="2024-01-08")
    dana.memberships.create(organization=house, start_date="2026-03-24")

    voters = import_votes("lower", "Henry", ["2026-03-17", "2026-03-23", "2026-03-24"])

    assert voters["2026-03-17"] == chance
    assert voters["2026-03-23"] == chance
    assert voters["2026-03-24"] is None


@pytest.mark.django_db
def test_each_same_surname_member_gets_the_votes_inside_their_own_term():
    # A seat changing hands mid-session between two members who both hold office now
    house = make_state("2025-01-08", "2025-12-31", "lower")
    first = Person.objects.create(name="Ann Cole", family_name="Cole", current_role=CURRENT)
    second = Person.objects.create(name="Ben Cole", family_name="Cole", current_role=CURRENT)
    first.memberships.create(organization=house, start_date="2023-01-01", end_date="2025-06-30")
    second.memberships.create(organization=house, start_date="2025-07-01")

    voters = import_votes("lower", "Cole", ["2025-05-01", "2025-06-30", "2025-07-01", "2025-08-01"])

    assert voters["2025-05-01"] == first
    assert voters["2025-06-30"] == first
    assert voters["2025-07-01"] == second
    assert voters["2025-08-01"] == second


@pytest.mark.django_db
def test_a_dated_other_name_counts_only_on_its_own_dates():
    house = make_state("2025-01-08", "2025-12-31", "lower")
    old = Person.objects.create(name="Pat Old", family_name="Old", current_role=CURRENT)
    new = Person.objects.create(name="Kim New", family_name="New", current_role=CURRENT)
    for p in (old, new):
        p.memberships.create(organization=house, start_date="2023-01-01")
    old.other_names.create(name="Mr. Speaker", start_date="2020-01-01", end_date="2025-03-10")
    # an undated other name on the same person must not lend its open dates to the dated one
    old.other_names.create(name="Speaker Old")
    new.other_names.create(name="Mr. Speaker", start_date="2025-03-11")

    voters = import_votes("lower", "Mr. Speaker", ["2025-03-05", "2025-03-10", "2025-03-11", "2025-04-01"])

    assert voters["2025-03-05"] == old
    assert voters["2025-03-10"] == old
    assert voters["2025-03-11"] == new
    assert voters["2025-04-01"] == new


@pytest.mark.django_db
def test_an_end_date_given_as_a_year_covers_that_year():
    house = make_state("2025-01-08", "2025-12-31", "lower")
    a = Person.objects.create(name="Ann Lee", family_name="Lee", current_role=CURRENT)
    b = Person.objects.create(name="Bo Lee", family_name="Lee", current_role=CURRENT)
    a.memberships.create(organization=house, start_date="2023-01-01")
    b.memberships.create(organization=house, start_date="2025-06-01")
    a.other_names.create(name="Leader", end_date="2025")
    b.other_names.create(name="Leader", start_date="2026-01-01")

    voters = import_votes("lower", "Leader", ["2025-07-01"])

    assert voters["2025-07-01"] == a


@pytest.mark.django_db
def test_a_name_unique_in_the_session_is_not_re_decided_by_the_date():
    # No currently linked vote changes: one person matches in the session, so the vote links to
    # them even on a day outside their stored term (people data dates are often a day off).
    house = make_state("2026-03-09", "2026-06-01", "lower")
    p = Person.objects.create(name="Jo Smith", family_name="Smith")
    p.memberships.create(organization=house, start_date="2026-03-24")

    voters = import_votes("lower", "Smith", ["2026-03-17"])

    assert voters["2026-03-17"] == p


@pytest.mark.django_db
def test_the_current_role_tie_break_still_wins_where_it_decides_today():
    # Today's tie-break (one of the two holds office now) already gives one answer; OPEN-767 keeps it.
    house = make_state("2026-03-09", "2026-06-01", "lower")
    now = Person.objects.create(name="Al Ray", family_name="Ray", current_role=CURRENT)
    before = Person.objects.create(name="Bea Ray", family_name="Ray")
    now.memberships.create(organization=house, start_date="2026-04-01")
    before.memberships.create(organization=house, start_date="2024-01-01", end_date="2026-03-31")

    voters = import_votes("lower", "Ray", ["2026-03-17"])

    assert voters["2026-03-17"] == now


@pytest.mark.django_db
def test_a_vote_dated_only_by_year_is_not_narrowed():
    house = make_state("2026-01-01", "2026-12-31", "lower")
    chance = Person.objects.create(name="Chance Henry", family_name="Henry", current_role=CURRENT)
    dana = Person.objects.create(name="Dana Henry", family_name="Henry", current_role=CURRENT)
    chance.memberships.create(organization=house, start_date="2024-01-08")
    dana.memberships.create(organization=house, start_date="2026-03-24")
    bi = BillImporter("jid")

    assert bi.resolve_person('~{"name": "Henry"}', "2026-01-01", "2026-12-31", "lower", date="2026") is None
    assert bi.resolve_person('~{"name": "Henry"}', "2026-01-01", "2026-12-31", "lower", date="") is None
    assert bi.resolve_person('~{"name": "Henry"}', "2026-01-01", "2026-12-31", "lower", date="2026-03-170") is None
    assert bi.resolve_person('~{"name": "Henry"}', "2026-01-01", "2026-12-31", "lower", date="2026-03-17") == chance.id
    # with a time after the day, as some scrapers store it
    assert (
        bi.resolve_person('~{"name": "Henry"}', "2026-01-01", "2026-12-31", "lower", date="2026-03-17T10:00:00")
        == chance.id
    )
    # and with no date at all (bill sponsors, events), today's answer: blank
    assert bi.resolve_person('~{"name": "Henry"}', "2026-01-01", "2026-12-31", "lower") is None
