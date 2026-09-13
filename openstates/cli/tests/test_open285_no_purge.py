"""OPEN-285: `os-people to-database` used to raise CancelTransaction() -- failing the ENTIRE
run for a jurisdiction -- whenever any previously-loaded person was absent from the current
run's source YAML, unless the operator explicitly passed --purge (which then deleted them).
That made a real, unattended weekly refresh unable to ever complete cleanly for a jurisdiction
with any legitimate roster turnover, and the only "fix" on offer was deleting people -- which
Ramon confirmed there's no reason to ever do. load_directory_to_database() now always leaves a
missing person's row untouched and never fails the run because of it, regardless of --purge
(which no longer exists as a parameter/flag at all).

Also covers the adjacent bug found in the same investigation: the merge path's raw SQL against
people_admin_unmatchedname/persondelta/personretirement, upstream tables this DDP fork has
never installed anywhere (confirmed empty/absent in every DDP Postgres -- Mac, RDS, and the old
pre-migration checkout) -- a real person-merge event (AZ, in production) crashed with
UndefinedTable hitting this code. Removed for this fork; the FK-constraint concern the original
comment cited doesn't apply when the referencing tables don't exist.
"""

from unittest import mock

import pytest

from openstates.data.models import Division, Jurisdiction, Organization
from openstates.data.models import Person as DjangoPerson
from openstates.cli.people import load_directory_to_database
from openstates.utils.people.to_database import cached_lookup, load_person
from openstates.models.people import Party, Person, Role

NC_JID = "ocd-jurisdiction/country:us/state:nc/government"


def setup_function():
    d = Division.objects.create(id="ocd-division/country:us/state:nc", name="NC")
    j = Jurisdiction.objects.create(id=NC_JID, name="NC", division=d)
    house = Organization.objects.create(name="House", classification="lower", jurisdiction=j)
    post_division = Division.objects.create(
        id="ocd-division/country:us/state:nc/sldl:1", name="1"
    )
    house.posts.create(label="1", division=post_division)
    Organization.objects.create(name="Democratic", classification="party")
    cached_lookup.cache_clear()


def _person(person_id: str, name: str) -> Person:
    return Person(
        id=person_id,
        name=name,
        party=[Party(name="Democratic")],
        roles=[Role(type="lower", jurisdiction=NC_JID, district="1")],
    )


@pytest.mark.django_db
def test_missing_person_is_left_in_database_not_deleted_and_does_not_fail_the_run():
    missing = _person("ocd-person/00000000-0000-1111-2222-100000000001", "Old Legislator")
    still_here = _person("ocd-person/00000000-0000-1111-2222-100000000002", "Current Legislator")
    load_person(missing)
    load_person(still_here)

    # Simulate this run's source YAML containing only `still_here` -- `missing` is absent,
    # same as a legislator who left office and was dropped from the people repo.
    with mock.patch(
        "openstates.cli.people.Person.load_yaml", return_value=still_here
    ):
        load_directory_to_database(["fake/present-0002.yml"])  # must not raise

    assert DjangoPerson.objects.filter(pk=missing.id).exists()
    assert DjangoPerson.objects.filter(pk=still_here.id).exists()


@pytest.mark.django_db
def test_load_directory_to_database_no_longer_accepts_a_purge_argument():
    """Guards the actual removal, not just the behavior change -- a caller passing purge=
    (the old signature) should fail loudly (TypeError) rather than silently ignore it."""
    with pytest.raises(TypeError):
        load_directory_to_database([], purge=True)  # type: ignore[call-arg]
