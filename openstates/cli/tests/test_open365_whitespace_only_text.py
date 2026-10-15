"""OPEN-365: a document whose extraction is only whitespace (an image-only or scanned PDF) is
not text. It must not take a diff, must not become the next version's baseline, and must be
stored as a failed extraction -- on the archive path and on the recompute path alike.

Real shape: Arizona has 131 such PDFs (126 with an HTML twin that has the real text), Michigan 1.
"""

import pytest

from openstates.cli.text_extract import (
    _has_text,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_recompute_same_media import FakeDoc
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

AZ = "ocd-jurisdiction/country:us/state:az/government"
FIRST = "Section 1. A court may consider the award.\n"
THIRD = "Section 1. A court shall consider the award and costs.\n"
BLANK = "\n \n\x0c\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("\n \n\t\x0c", False),
        ("  ", False),  # non-breaking and em space: str.strip() treats them as blank
        ("a", True),
        ("  a  ", True),
        ("A\n", True),  # North Carolina's real 3-character stamp text is still text
    ],
)
def test_has_text(text, expected):
    assert _has_text(text) is expected


def _three_version_arizona_bill():
    bill = _make_bill(jid=AZ, jurisdiction_name="Arizona")
    bill.classification = ["bill"]
    bill.save()
    for note, url in (
        ("Introduced Version", "https://x.test/az/introduced.pdf"),
        ("House Engrossed Version", "https://x.test/az/engrossed.pdf"),
        ("Chaptered Version", "https://x.test/az/chaptered.pdf"),
    ):
        bill.versions.create(note=note, date="").links.create(
            url=url, media_type="application/pdf"
        )
    return bill


@pytest.mark.django_db
class TestArchive:
    def test_blank_document_is_a_failed_extraction_and_not_a_baseline(self):
        bill = _three_version_arizona_bill()
        _archive(
            bill,
            {
                "https://x.test/az/introduced.pdf": FIRST,
                "https://x.test/az/engrossed.pdf": BLANK,
                "https://x.test/az/chaptered.pdf": THIRD,
            },
        )
        docs = {
            d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)
        }

        blank = docs["House Engrossed Version"]
        assert blank.is_error is True
        assert blank.diff_from_previous_version is None
        assert blank.raw_text == BLANK  # the extraction is kept as found, only flagged

        # The next real version diffs against the last real text, not against the blank.
        diff = docs["Chaptered Version"].diff_from_previous_version
        assert diff is not None
        assert "-Section 1. A court may consider the award." in diff
        assert "+Section 1. A court shall consider the award and costs." in diff

    def test_real_documents_are_unaffected(self):
        bill = _three_version_arizona_bill()
        _archive(
            bill,
            {
                "https://x.test/az/introduced.pdf": FIRST,
                "https://x.test/az/engrossed.pdf": THIRD,
                "https://x.test/az/chaptered.pdf": THIRD
                + "Section 2. Effective July 1.\n",
            },
        )
        docs = {
            d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)
        }
        assert all(d.is_error is False for d in docs.values())
        assert docs["House Engrossed Version"].diff_from_previous_version
        assert "+Section 2. Effective July 1." in (
            docs["Chaptered Version"].diff_from_previous_version
        )


def test_recompute_treats_an_already_stored_blank_row_as_no_text():
    """The 131 stored Arizona rows have is_error=False. A recompute must not need them flipped
    first: it ignores them the same way the archive path now would."""
    docs = [
        FakeDoc("Introduced Version", "application/pdf", FIRST),
        FakeDoc(
            "House Engrossed Version", "application/pdf", BLANK, diff="-old\n+blank"
        ),
        FakeDoc("Chaptered Version", "application/pdf", THIRD, diff="-blank\n+all"),
    ]
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Arizona", is_bill=True
    )
    changed = {d.version_note: new for d, new in result["changed"]}

    assert changed["House Engrossed Version"] is None
    assert "-Section 1. A court may consider the award." in (
        changed["Chaptered Version"]
    )


@pytest.mark.django_db
def test_archive_and_recompute_agree_on_a_blank_document():
    bill = _three_version_arizona_bill()
    _archive(
        bill,
        {
            "https://x.test/az/introduced.pdf": FIRST,
            "https://x.test/az/engrossed.pdf": BLANK,
            "https://x.test/az/chaptered.pdf": THIRD,
        },
    )
    stored = list(BillVersionDocument.objects.filter(bill=bill))
    result = recomputed_diffs_for_documents(
        stored, jurisdiction_name="Arizona", is_bill=True
    )
    assert result["changed"] == []
