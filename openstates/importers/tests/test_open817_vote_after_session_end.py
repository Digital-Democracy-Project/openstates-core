"""OPEN-817: a vote held after its session ended (a veto override) can match a member seated after the session ended.

Real shape (production replica, 2026-11-23): Maryland's 2026 regular session ran 2026-01-14 to 2026-04-13. Its veto
overrides were voted on 2026-08-03 and 08-04 and are stored on the 2026 bills, so the vote's session is the regular
one. A delegate seated on 2026-08-03 voted on both; the session-dated lookup found no one, so the votes stayed blank.
"""
import pytest
from openstates.scrape import VoteEvent as ScrapeVoteEvent, Bill as ScrapeBill
from openstates.importers import VoteEventImporter, BillImporter
from openstates.data.models import Jurisdiction, Person, Organization, Division, VoteEvent

CURRENT = {"title": "Delegate", "district": "1"}
SESSION_START, SESSION_END = "2026-01-14", "2026-04-13"


def make_state(chamber="lower"):
    Division.objects.create(id="ocd-division/country:us", name="USA")
    j = Jurisdiction.objects.create(id="jid", division_id="ocd-division/country:us")
    j.legislative_sessions.create(name="S", identifier="S", start_date=SESSION_START, end_date=SESSION_END)
    return Organization.objects.create(name=chamber, classification=chamber, jurisdiction=j)


def import_votes(voter_name, dates, chamber="lower"):
    """One roll call per date, each with `voter_name` voting yes. Returns {date: voter}."""
    bill = ScrapeBill("HB 1", "S", "A bill", chamber=chamber)
    events = []
    for n, day in enumerate(dates):
        ve = ScrapeVoteEvent(
            legislative_session="S",
            motion_text=f"Shall the Bill pass notwithstanding the objections of the Chief Executive? {n}",
            identifier=f"RC{n}",
            start_date=day,
            classification="veto-override",
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


@pytest.mark.django_db
def test_vote_after_session_end_matches_a_member_seated_after_the_session_ended():
    house = make_state()
    late = Person.objects.create(name="Lee Late", family_name="Late", current_role=CURRENT)
    late.memberships.create(organization=house, start_date="2026-08-03")

    voters = import_votes("Late", ["2026-08-03", "2026-08-04", "2026-08-04T11:00:00"])

    assert voters["2026-08-03"] == late
    assert voters["2026-08-04"] == late
    assert voters["2026-08-04T11:00:00"] == late


@pytest.mark.django_db
def test_vote_inside_the_session_is_unchanged():
    # The same member, a vote inside the session: still no one (he was not seated in it), as before
    house = make_state()
    late = Person.objects.create(name="Lee Late", family_name="Late", current_role=CURRENT)
    late.memberships.create(organization=house, start_date="2026-08-03")
    other = Person.objects.create(name="Ann Sat", family_name="Sat", current_role=CURRENT)
    other.memberships.create(organization=house, start_date="2023-01-11")

    assert import_votes("Late", ["2026-03-01", "2026-04-13"]) == {"2026-03-01": None, "2026-04-13": None}
    VoteEvent.objects.all().delete()
    assert import_votes("Sat", ["2026-03-01"]) == {"2026-03-01": other}
    # seated on the session's last day, voting that day: the session lookup (start before the end) is kept as is
    last = Person.objects.create(name="Lou Last", family_name="Last", current_role=CURRENT)
    last.memberships.create(organization=house, start_date=SESSION_END)
    VoteEvent.objects.all().delete()
    assert import_votes("Last", [SESSION_END]) == {SESSION_END: None}


@pytest.mark.django_db
def test_no_match_when_the_membership_does_not_cover_the_vote_date():
    house = make_state()
    later = Person.objects.create(name="Lee Later", family_name="Later", current_role=CURRENT)
    later.memberships.create(organization=house, start_date="2026-08-05")
    # seated after the session, but gone before the vote
    gone = Person.objects.create(name="Gus Gone", family_name="Gone")
    gone.memberships.create(organization=house, start_date="2026-05-01", end_date="2026-07-31")
    # the other chamber's seat does not count for a House vote: a delegate seated after the session who
    # moved to the Senate before the vote held a seat that day, but not a House one
    senate = Organization.objects.create(name="upper", classification="upper", jurisdiction_id="jid")
    sen = Person.objects.create(name="Sue Senate", family_name="Senate", current_role=CURRENT)
    sen.memberships.create(organization=house, start_date="2026-05-01", end_date="2026-07-31")
    sen.memberships.create(organization=senate, start_date="2026-08-01")

    assert import_votes("Later", ["2026-08-04"]) == {"2026-08-04": None}
    VoteEvent.objects.all().delete()
    assert import_votes("Gone", ["2026-08-04"]) == {"2026-08-04": None}
    VoteEvent.objects.all().delete()
    assert import_votes("Senate", ["2026-08-04"]) == {"2026-08-04": None}


@pytest.mark.django_db
def test_the_vote_date_settles_a_session_member_and_a_late_seated_member_sharing_a_name():
    # A member who left after the session and a same-surname member seated in the seat later, both
    # now out of office: the OPEN-767 date rule gives each override vote to whoever sat that day.
    house = make_state()
    left = Person.objects.create(name="Ann Cole", family_name="Cole")
    came = Person.objects.create(name="Ben Cole", family_name="Cole")
    left.memberships.create(organization=house, start_date="2023-01-11", end_date="2026-06-30")
    came.memberships.create(organization=house, start_date="2026-07-15")

    voters = import_votes("Cole", ["2026-06-01", "2026-08-03"])

    assert voters["2026-06-01"] == left
    assert voters["2026-08-03"] == came
