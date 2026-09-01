"""OPEN-224: a short procedural document (Virginia's "Governor's Recommendation", a numbered
Utah "House Amendment N" excerpt) must neither take a diff nor become the next version's
baseline -- the same treatment archive_bill_versions()/recomputed_diffs_for_documents() already
give a version whose note doesn't match any known stage (_STAGE_UNKNOWN, OPEN-34).

Unit coverage for is_procedural_document() itself lives in
openstates/utils/tests/test_version_ordering.py. This file is the integration half: does the
real archive-time diff chain, and the recompute path, both actually skip these documents and
close the lineage gap correctly (AC2/AC3), and do the two diff-computing paths still agree
(AC7, matching test_prediff_cleaning_parity.py's own OPEN-219 precedent)?
"""

from unittest import mock

import pytest

from openstates.cli.text_extract import (
    archive_bill_versions,
    recomputed_diffs_for_documents,
)
from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_text_extract import _make_bill


def _archive(bill, texts_by_url):
    def fake_fetch(url):
        return texts_by_url[url].encode("utf-8")

    with mock.patch(
        "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch
    ), mock.patch(
        "openstates.cli.text_extract.get_extract_func",
        side_effect=lambda md: (lambda data, meta: data.decode("utf-8")),
    ), mock.patch(
        "openstates.cli.text_extract._upload_and_verify", return_value=None
    ), mock.patch(
        "openstates.cli.text_extract._block_page_reason", return_value=None
    ), mock.patch(
        "os.makedirs"
    ), mock.patch(
        "builtins.open", mock.mock_open()
    ):
        return archive_bill_versions(bill)


INTRODUCED_URL = "https://x.test/introduced.pdf"
PROCEDURAL_URL = "https://x.test/procedural.pdf"
ENROLLED_URL = "https://x.test/enrolled.pdf"

# Deliberately real-shaped: a short procedural note whose own "text" bears no real
# relationship to the bill (this is what a genuine Governor's Recommendation/Conference
# Report looks like -- a short instruction, not a re-typeset bill), sandwiched between two
# much larger, related bill texts.
INTRODUCED_TEXT = "Section 1. A court may consider the tax consequences of an award.\n"
PROCEDURAL_TEXT = "Amend by striking 'may' and inserting 'shall' in Section 1.\n"
ENROLLED_TEXT = "Section 1. A court shall consider the tax consequences of an award.\n"


def _make_three_version_bill(jurisdiction_name, jid, procedural_note):
    bill = _make_bill(jid=jid, jurisdiction_name=jurisdiction_name)
    bill.classification = ["bill"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=INTRODUCED_URL, media_type="application/pdf")
    v2 = bill.versions.create(note=procedural_note, date="")
    v2.links.create(url=PROCEDURAL_URL, media_type="application/pdf")
    v3 = bill.versions.create(note="Enrolled", date="")
    v3.links.create(url=ENROLLED_URL, media_type="application/pdf")
    return bill


@pytest.mark.django_db
class TestProceduralDocumentSkipsDiffAndBaseline:
    """AC2/AC3: a real three-version chain, Introduced -> [procedural document] -> Enrolled,
    for each of this ticket's two evidence-backed jurisdictions."""

    @pytest.mark.parametrize(
        ("jurisdiction_name", "jid", "procedural_note"),
        [
            (
                "Virginia",
                "ocd-jurisdiction/country:us/state:va/government",
                "Governor's Recommendation",
            ),
            (
                "Utah",
                "ocd-jurisdiction/country:us/state:ut/government",
                "House Amendment 1",
            ),
        ],
    )
    def test_archive_skips_procedural_and_diffs_enrolled_against_introduced(
        self, jurisdiction_name, jid, procedural_note
    ):
        bill = _make_three_version_bill(jurisdiction_name, jid, procedural_note)
        _archive(
            bill,
            {
                INTRODUCED_URL: INTRODUCED_TEXT,
                PROCEDURAL_URL: PROCEDURAL_TEXT,
                ENROLLED_URL: ENROLLED_TEXT,
            },
        )
        docs = {
            d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)
        }

        # AC2: the procedural document itself never gets a diff.
        assert docs[procedural_note].diff_from_previous_version is None

        # AC3: Enrolled diffs against Introduced's real text, not against the procedural
        # document's unrelated text, and not against nothing.
        enrolled_diff = docs["Enrolled"].diff_from_previous_version
        assert enrolled_diff is not None
        assert "+Section 1. A court shall consider the tax consequences of an award." in (
            enrolled_diff
        )
        assert "-Section 1. A court may consider the tax consequences of an award." in (
            enrolled_diff
        )
        # The procedural document's own (unrelated) text must not leak into the diff at all --
        # proof it was never used as a baseline or a comparison target.
        assert "striking" not in enrolled_diff
        assert "inserting" not in enrolled_diff

    def test_without_the_fix_this_would_be_a_degenerate_whole_document_diff(self):
        """Demonstrates the bug this test class guards against: diffing Enrolled directly
        against the procedural document's own short, unrelated text (what happened before this
        ticket) produces a diff that reads as replacing the entire document, not the real
        one-word change Enrolled actually contains relative to Introduced."""
        import difflib

        naive_diff = "\n".join(
            difflib.unified_diff(
                PROCEDURAL_TEXT.splitlines(), ENROLLED_TEXT.splitlines(), lineterm=""
            )
        )
        # Confirms the fixture is a real regression case, not a no-op: the naive diff really
        # does show the procedural document's own line being removed wholesale.
        assert "-Amend by striking 'may' and inserting 'shall' in Section 1." in naive_diff


@pytest.mark.django_db
class TestProceduralDocumentBothDiffPathsAgree:
    """AC7, matching test_prediff_cleaning_parity.py's own OPEN-219 assertion shape: archive,
    then recompute from the stored rows, and require the recompute to report nothing changed."""

    @pytest.mark.parametrize(
        ("jurisdiction_name", "jid", "procedural_note"),
        [
            (
                "Virginia",
                "ocd-jurisdiction/country:us/state:va/government",
                "Conference Report",
            ),
            (
                "Utah",
                "ocd-jurisdiction/country:us/state:ut/government",
                "Senate Amendment 2",
            ),
        ],
    )
    def test_recompute_agrees_with_archive(self, jurisdiction_name, jid, procedural_note):
        bill = _make_three_version_bill(jurisdiction_name, jid, procedural_note)
        _archive(
            bill,
            {
                INTRODUCED_URL: INTRODUCED_TEXT,
                PROCEDURAL_URL: PROCEDURAL_TEXT,
                ENROLLED_URL: ENROLLED_TEXT,
            },
        )
        docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))

        result = recomputed_diffs_for_documents(
            docs, jurisdiction_name=jurisdiction_name, is_bill=True
        )

        assert result["changed"] == [], (
            f"{jurisdiction_name}/{procedural_note}: recompute disagrees with the archiver "
            f"for {len(result['changed'])} document(s)"
        )
        assert len(result["unchanged"]) == len(docs)


@pytest.mark.django_db
def test_amendment_in_the_nature_of_a_substitute_still_gets_a_real_diff():
    """AC6's named regression case: this Virginia note is a genuine full-replacement text and
    must keep behaving exactly like Enrolled/Introduced -- not be swept up by this ticket's
    exclusion list just because it contains the word "Amendment"."""
    bill = _make_bill(
        jid="ocd-jurisdiction/country:us/state:va/government", jurisdiction_name="Virginia"
    )
    bill.classification = ["bill"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=INTRODUCED_URL, media_type="application/pdf")
    v2 = bill.versions.create(note="Amendment in the Nature of a Substitute", date="")
    v2.links.create(url=ENROLLED_URL, media_type="application/pdf")

    _archive(bill, {INTRODUCED_URL: INTRODUCED_TEXT, ENROLLED_URL: ENROLLED_TEXT})

    doc = BillVersionDocument.objects.get(
        bill=bill, version_note="Amendment in the Nature of a Substitute"
    )
    assert doc.diff_from_previous_version is not None
    assert "+Section 1. A court shall consider the tax consequences of an award." in (
        doc.diff_from_previous_version
    )
