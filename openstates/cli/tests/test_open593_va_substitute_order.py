"""OPEN-593: Virginia's committee, subcommittee and floor substitutes all rank as amendments with no
date, so they tied and kept the database's walk order, which for Virginia is backward: 108 bills of the
2026 session showed the wrong version as the latest. Their LIS file numbers
(lis.blob.core.windows.net/files/<n>.PDF) rise as they are published and now order them; a
"Subcommittee #3" or "Secondary Education" in the note is not read as an ordinal for Virginia.

The bills below are real (Mac replica, 2026 session), stored in the backward order the database
returned them in.
"""

import pytest

from openstates.cli.text_extract import recomputed_diffs_for_documents
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import (
    STAGE_AMENDMENT,
    document_number,
    note_stage,
    version_sort_key,
)

from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_va_bill


def _lis(n, ext="PDF"):
    return f"https://lis.blob.core.windows.net/files/{n}.{ext}"


def _links(*numbers):
    """A version's links as the scraper stores them: each PDF with its .HTML twin at n+1."""
    urls = []
    for n in numbers:
        urls += [_lis(n), _lis(n + 1, "HTML")]
    return urls


# (note, LIS file numbers), in the order the database walks them -- backward.
HB1432 = [
    ("Delegate Mehta                Substitute", [1150630]),  # the note's spacing is LIS's own
    ("Courts of Justice Substitute", [1142380, 1135485]),  # re-issued: two files
    ("Criminal Subcommittee Substitute", [1136021]),
    ("Introduced", [1094838]),
]
HB1432_TRUE = [
    "Introduced",
    "Criminal Subcommittee Substitute",
    "Courts of Justice Substitute",
    "Delegate Mehta                Substitute",
]
HB560 = [
    ("Appropriations Substitute", [1148700]),
    ("Health & Human Resources Subcommittee Substitute", [1148118]),
    ("Health and Human Services Substitute", [1137092]),
    ("Social Services Subcommittee Substitute", [1132616]),
    ("Introduced", [1084941]),
]
HB560_TRUE = [
    "Introduced",
    "Social Services Subcommittee Substitute",
    "Health and Human Services Substitute",
    "Health & Human Resources Subcommittee Substitute",
    "Appropriations Substitute",
]
# HB 84: the House subcommittee (2026-01-22), the House committee (01-27), then the Senate committee
# (02-23). "Subcommittee #3" used to read as ordinal 3 and sort after both committees.
HB84 = [
    ("Commerce and Labor Substitute", [1177678]),
    ("Labor and Commerce Substitute", [1098716]),
    ("Subcommittee #3 Subcommittee Substitute", [1094675]),
    ("Introduced", [1082322]),
    ("Engrossed", [1104161]),
]
HB84_TRUE = [
    "Introduced",
    "Subcommittee #3 Subcommittee Substitute",
    "Labor and Commerce Substitute",
    "Commerce and Labor Substitute",
    "Engrossed",
]


def _va_order(versions, jurisdiction_name="Virginia"):
    return [
        note
        for note, _ in sorted(
            versions,
            key=lambda v: version_sort_key(
                v[0], "", _links(*v[1]), "house", jurisdiction_name
            ),
        )
    ]


def test_document_number_reads_the_lis_file_number_largest_first():
    assert document_number([_lis(1136021)]) == 1136021
    assert document_number([_lis(1136022, "HTML")]) == 1136022
    # a re-issued substitute: the newest file is the one the version stands for
    assert document_number(_links(1142380, 1135485)) == 1142381
    assert document_number(["https://budget.lis.virginia.gov/get/amendment/1/"]) == 0
    assert document_number(["https://lis.blob.core.windows.net/files/notanumber.PDF"]) == 0


def test_arizona_keeps_its_smallest_number():
    az = "https://apps.azleg.gov/BillStatus/GetDocumentPdf/"
    assert document_number([az + "9", az + "5"]) == 5


@pytest.mark.parametrize(
    ("walk_order", "true_order"),
    [(HB1432, HB1432_TRUE), (HB560, HB560_TRUE), (HB84, HB84_TRUE)],
    ids=["HB1432", "HB560", "HB84"],
)
def test_virginia_substitutes_are_ordered_by_lis_file_number(walk_order, true_order):
    assert _va_order(walk_order) == true_order
    # whatever order they arrive in
    assert _va_order(list(reversed(walk_order))) == true_order


def test_sb345_crosses_chambers_by_file_number():
    walk = [
        ("Civil Subcommittee Substitute", [1181789]),
        ("Courts of Justice Substitute", [1132549]),
        ("Introduced", [1089714]),
    ]
    assert _va_order(walk) == [
        "Introduced",
        "Courts of Justice Substitute",
        "Civil Subcommittee Substitute",
    ]


def test_virginia_subcommittee_number_and_secondary_are_not_ordinals():
    for note in (
        "Subcommittee #3 Subcommittee Substitute",
        "Elementary & Secondary Education Subcommittee Substitute",
    ):
        assert note_stage(note)[1] > 0  # what the generic rule reads
        key = version_sort_key(note, "", [_lis(1)], None, "Virginia")
        assert key[0] == STAGE_AMENDMENT and key[2] == 0.0


def test_the_ordinal_reset_is_virginia_only():
    # Utah's "Substitute #3" is a real ordinal and keeps it
    assert version_sort_key("Substitute #3", "", [], None, "Utah")[2] == 3.0
    # Virginia's other stages keep theirs
    assert version_sort_key("Reengrossed", "", [], None, "Virginia") == version_sort_key(
        "Reengrossed", "", []
    )


def test_stages_still_lead_the_file_number():
    # Introduced's file is after the substitute's here; the stage still puts it first
    walk = [("Courts of Justice Substitute", [1000]), ("Introduced", [2000])]
    assert _va_order(walk) == ["Introduced", "Courts of Justice Substitute"]


INTRO = "Section 1. A court may order restitution.\n"
CRIMINAL = "Section 1. A court may order restitution to a victim.\n"
COURTS = "Section 1. A court may order restitution to a victim within one year.\n"
MEHTA = "Section 1. A court shall order restitution to a victim within one year.\n"
TEXTS = {_lis(1094838): INTRO, _lis(1136021): CRIMINAL, _lis(1142380): COURTS, _lis(1150630): MEHTA}


def _hb1432_stored_backward():
    bill = _make_va_bill()
    bill.identifier = "HB 1432"
    bill.classification = ["bill"]
    bill.save()
    for note, n in [
        ("Delegate Mehta                Substitute", 1150630),
        ("Courts of Justice Substitute", 1142380),
        ("Criminal Subcommittee Substitute", 1136021),
        ("Introduced", 1094838),
    ]:
        bill.versions.create(note=note, date="").links.create(
            url=_lis(n), media_type="application/pdf"
        )
    return bill


@pytest.mark.django_db
def test_archive_diffs_each_substitute_against_the_one_before_it():
    bill = _hb1432_stored_backward()
    _archive(bill, TEXTS)

    def diff(note):
        return BillVersionDocument.objects.get(
            bill=bill, version_note=note
        ).diff_from_previous_version

    assert "-" + INTRO in diff("Criminal Subcommittee Substitute")
    assert "-" + CRIMINAL in diff("Courts of Justice Substitute")
    assert "-" + COURTS in diff("Delegate Mehta                Substitute")
    assert "+" + MEHTA.strip() in diff("Delegate Mehta                Substitute")


@pytest.mark.django_db
def test_recompute_agrees_with_archive_for_virginia_substitutes():
    bill = _hb1432_stored_backward()
    _archive(bill, TEXTS)
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))

    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Virginia", is_bill=True, bill_identifier="HB 1432"
    )

    assert result["changed"] == []
    assert len(result["unchanged"]) == len(docs)
