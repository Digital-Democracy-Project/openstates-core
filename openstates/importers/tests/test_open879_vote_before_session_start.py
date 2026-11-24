"""OPEN-879: a vote dated before its session began can match a member whose seat had ended before the session began.

Real shape (production replica, 2026-11-24): Hawaii's 2026 regular session runs 2026-01-21 to 2026-05-08, and its bill
pages carry the bills' 2025 roll calls, stored in the 2026 session. A representative whose seat ended on 2025-04-23 and
one who moved to the Senate on 2025-12-03 voted in those roll calls; the session-dated lookup found no one in either
chamber seat, so 3,567 votes stayed blank. The lookup is tried again on who held the seat on the vote's day, only when
the session's window finds no one.
"""
import pytest
from openstates.scrape import VoteEvent as ScrapeVoteEvent, Bill as ScrapeBill
from openstates.importers import VoteEventImporter, BillImporter
from openstates.data.models import Jurisdiction, Person, Organization, Division, VoteEvent

CURRENT = {"title": "Representative", "district": "1"}
SESSION_START, SESSION_END = "2026-01-21", "2026-05-08"


def make_state():
    Division.objects.create(id="ocd-division/country:us", name="USA")
    j = Jurisdiction.objects.create(id="jid", division_id="ocd-division/country:us")
    j.legislative_sessions.create(name="S", identifier="S", start_date=SESSION_START, end_date=SESSION_END)
    house = Organization.objects.create(name="lower", classification="lower", jurisdiction=j)
    senate = Organization.objects.create(name="upper", classification="upper", jurisdiction=j)
    return house, senate


def import_votes(voter_name, dates, chamber="lower"):
    """One roll call per date, each with `voter_name` voting yes. Returns {date: voter}."""
    bill = ScrapeBill("HB 1", "S", "A bill", chamber=chamber)
    events = []
    for n, day in enumerate(dates):
        ve = ScrapeVoteEvent(
            legislative_session="S",
            motion_text=f"Passage {n}",
            identifier=f"RC{n}",
            start_date=day,
            classification="passage",
            result="pass",
            bill="HB 1",
            bill_chamber=chamber,
            chamber=chamber,
        )
        ve.yes(voter_name)
        events.append(ve.as_dict())
    bi = BillImporter("jid")
    bi.import_data([bill.as_dict()])
    VoteEventImporter("jid", bi).import_data(events)
    return {ve.start_date: ve.votes.get().voter for ve in VoteEvent.objects.all()}


def reset():
    VoteEvent.objects.all().delete()


@pytest.mark.django_db
def test_vote_before_session_start_matches_a_member_whose_seat_ended_before_the_session():
    house, _ = make_state()
    ward = Person.objects.create(name="Gene Ward", family_name="Ward")
    ward.memberships.create(organization=house, start_date="2022-11-08", end_date="2025-04-23")

    voters = import_votes("Ward", ["2025-02-03", "2025-04-23", "2025-04-23T10:00:00"])

    assert voters["2025-02-03"] == ward
    assert voters["2025-04-23"] == ward  # the seat's last day counts
    assert voters["2025-04-23T10:00:00"] == ward


@pytest.mark.django_db
def test_member_who_changed_chamber_is_matched_in_the_chamber_that_held_the_vote():
    house, senate = make_state()
    lam = Person.objects.create(name="Rachele Lamosao", family_name="Lamosao", current_role=CURRENT)
    lam.memberships.create(organization=house, start_date="2024-11-05", end_date="2025-12-03")
    lam.memberships.create(organization=senate, start_date="2025-12-03")

    assert import_votes("Lamosao", ["2025-01-30"], chamber="lower") == {"2025-01-30": lam}
    reset()
    # a House member whose seat ended before the session is not matched on a Senate roll call
    ward = Person.objects.create(name="Gene Ward", family_name="Ward")
    ward.memberships.create(organization=house, start_date="2022-11-08", end_date="2025-04-23")
    assert import_votes("Ward", ["2025-02-03"], chamber="upper") == {"2025-02-03": None}


@pytest.mark.django_db
def test_no_match_when_the_seat_did_not_cover_the_vote_day_or_the_date_is_partial():
    house, _ = make_state()
    gone = Person.objects.create(name="Gus Gone", family_name="Gone")
    gone.memberships.create(organization=house, start_date="2020-01-01", end_date="2025-03-01")

    assert import_votes("Gone", ["2025-04-01"]) == {"2025-04-01": None}  # left before the vote
    reset()
    assert import_votes("Gone", ["2025"]) == {"2025": None}  # a year alone places no one


@pytest.mark.django_db
def test_vote_inside_the_session_is_unchanged():
    # the same member who matches before the session does not match inside it: his seat had ended
    house, _ = make_state()
    ward = Person.objects.create(name="Gene Ward", family_name="Ward")
    ward.memberships.create(organization=house, start_date="2022-11-08", end_date="2025-04-23")

    assert import_votes("Ward", ["2026-03-01"]) == {"2026-03-01": None}


@pytest.mark.django_db
def test_a_name_the_session_already_matches_is_never_changed_by_a_vote_before_the_session():
    # Two members share a surname: one sits in the session, the other's seat ended before it. A roll call dated
    # before the session still goes to the member the session's window finds (as before this change), not to
    # whoever held a seat that day, which would make the name ambiguous.
    house, _ = make_state()
    sitting = Person.objects.create(name="Ann Cole", family_name="Cole")
    sitting.memberships.create(organization=house, start_date="2024-11-05")
    former = Person.objects.create(name="Ben Cole", family_name="Cole")
    former.memberships.create(organization=house, start_date="2020-11-03", end_date="2025-06-30")

    assert import_votes("Cole", ["2025-02-03"]) == {"2025-02-03": sitting}
