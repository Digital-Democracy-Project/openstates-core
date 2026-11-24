"""OPEN-809: a carried-over bill's sponsor is matched on the day the bill was introduced when this session's members
give nobody.

Real shape (production replica, 2026-11-23): Hawaii's 2026 session (2026-01-21 to 2026-05-08) holds bills first
acted on in January 2025 and carried over. Rep. Gene Ward (House, until 2025-04-23) and Sen. Henry Aquino (Senate,
until 2025-11-30) left before 2026 began, so their 396 sponsorships ("WARD", "AQUINO") matched nobody.
"""
import pytest
from openstates.scrape import Bill as ScrapeBill
from openstates.importers import BillImporter
from openstates.importers.bills import carried_over_since
from openstates.data.models import Jurisdiction, Person, Organization, Division, Bill

CURRENT = {"title": "Representative", "district": "1"}


@pytest.fixture
def hawaii():
    Division.objects.create(id="ocd-division/country:us", name="USA")
    j = Jurisdiction.objects.create(id="jid", division_id="ocd-division/country:us")
    j.legislative_sessions.create(name="2026", identifier="2026", start_date="2026-01-21", end_date="2026-05-08")
    Organization.objects.create(name="Senate", classification="upper", jurisdiction=j)
    return Organization.objects.create(name="House", classification="lower", jurisdiction=j)


def import_bill(sponsor, first_action):
    bill = ScrapeBill("HB 599", "2026", "A bill", chamber="lower")
    bill.add_action("Introduced", first_action, chamber="lower")
    bill.add_sponsorship(sponsor, "primary", "person", True)
    BillImporter("jid").import_data([bill.as_dict()])
    return Bill.objects.get().sponsorships.get().person


@pytest.mark.django_db
def test_a_sponsor_who_left_before_the_session_links_on_a_carried_over_bill(hawaii):
    ward = Person.objects.create(name="Gene Ward", family_name="Ward")
    ward.memberships.create(organization=hawaii, start_date="2022-11-08", end_date="2025-04-23")

    assert import_bill("WARD", "2025-01-17") == ward


@pytest.mark.django_db
def test_a_seat_starting_or_ending_on_the_introduction_day_counts(hawaii):
    starts = Person.objects.create(name="Al Day", family_name="Day")
    starts.memberships.create(organization=hawaii, start_date="2025-01-17", end_date="2025-06-30")
    ends = Person.objects.create(name="Bo Eve", family_name="Eve")
    ends.memberships.create(organization=hawaii, start_date="2022-11-08", end_date="2025-01-17")

    assert import_bill("Day", "2025-01-17") == starts
    Bill.objects.all().delete()
    assert import_bill("Eve", "2025-01-17") == ends


@pytest.mark.django_db
def test_a_sponsor_seated_in_the_session_keeps_the_session_match(hawaii):
    # On the day the bill was introduced only the earlier Lee sat; in 2026 only Mike Lee does. The session's
    # answer stands: a sponsor that matches today is never re-decided by the introduction date.
    earlier = Person.objects.create(name="Ann Lee", family_name="Lee")
    earlier.memberships.create(organization=hawaii, start_date="2010-11-02", end_date="2025-11-30")
    mike = Person.objects.create(name="Mike Lee", family_name="Lee", current_role=CURRENT)
    mike.memberships.create(organization=hawaii, start_date="2025-06-01")

    assert import_bill("Lee", "2025-01-17") == mike


@pytest.mark.django_db
def test_a_bill_introduced_in_this_session_is_not_matched_by_an_earlier_day(hawaii):
    ward = Person.objects.create(name="Gene Ward", family_name="Ward")
    ward.memberships.create(organization=hawaii, start_date="2022-11-08", end_date="2025-04-23")

    assert import_bill("WARD", "2026-01-22") is None


@pytest.mark.django_db
def test_a_name_several_session_members_share_is_left_blank(hawaii):
    # Two Kims both sit in 2026 (no single answer); only one sat on the day the bill was introduced. The rule is
    # for sponsors the session matches to nobody, so this stays blank as before.
    a = Person.objects.create(name="Al Kim", family_name="Kim", current_role=CURRENT)
    b = Person.objects.create(name="Bo Kim", family_name="Kim", current_role=CURRENT)
    a.memberships.create(organization=hawaii, start_date="2020-11-03")
    b.memberships.create(organization=hawaii, start_date="2025-06-01")

    assert import_bill("Kim", "2025-01-17") is None


@pytest.mark.django_db
def test_two_members_seated_on_the_introduction_day_are_settled_only_by_that_day(hawaii):
    # Both left before 2026; on the introduction day the name fits two seated members and neither holds office
    # now, so it stays blank rather than guessed.
    for first in ("Al", "Bo"):
        p = Person.objects.create(name=f"{first} Ono", family_name="Ono")
        p.memberships.create(organization=hawaii, start_date="2020-11-03", end_date="2025-11-30")

    assert import_bill("Ono", "2025-01-17") is None


@pytest.mark.django_db
def test_a_dated_other_name_counts_on_the_introduction_day(hawaii):
    # OPEN-767's day match settles a tie on the introduction day: "Speaker" was one member's name until a date
    # and the other's after it, and both left before 2026.
    old = Person.objects.create(name="Pat Old", family_name="Old")
    new = Person.objects.create(name="Kim New", family_name="New")
    for p in (old, new):
        p.memberships.create(organization=hawaii, start_date="2020-11-03", end_date="2025-11-30")
    old.other_names.create(name="Speaker", end_date="2025-03-10")
    new.other_names.create(name="Speaker", start_date="2025-03-11")

    assert import_bill("Speaker", "2025-01-17") == old


def test_carried_over_since_is_the_first_full_date_before_the_session():
    actions = [{"date": "2025-12-01"}, {"date": "2025-01-17T09:00:00"}, {"date": "2025"}, {"date": ""}]
    assert carried_over_since(actions, "2026-01-21") == "2025-01-17"
    assert carried_over_since(actions, "2025-01-17") == ""
    assert carried_over_since([{"date": "2026-01-22"}], "2026-01-21") == ""
    assert carried_over_since([{"date": "2025"}], "2026-01-21") == ""
    assert carried_over_since([], "2026-01-21") == ""
    assert carried_over_since(actions, "") == ""
    assert carried_over_since(actions, "2026") == ""
    # a day that does not exist is skipped, not an import error
    assert carried_over_since([{"date": "2025-02-30"}, {"date": "2025-03-02"}], "2026-01-21") == "2025-03-02"
