"""OPEN-217: `archive_bill_versions()` must diff each document against the previous version's
document of its OWN media type, not against one shared baseline.

End-to-end against the real function and a real test database, using the same fetch/extract
mocking pattern as `TestArchiveBillVersionsRegressionOPEN34` in `test_text_extract.py` — the
defect lives in how the archive walk carries state between versions, so a stubbed-out unit of
that walk would not have caught it. `test_recompute_same_media.py` covers the sibling recompute
path, which OPEN-211 already fixed.
"""

import re
from unittest import mock

import pytest

from openstates.cli.text_extract import archive_bill_versions
from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_text_extract import _make_bill


# Two renderings of the same bill. A PDF and an XML extraction of one version never match each
# other character for character — different extractors, different line breaks — which is exactly
# why comparing across them produces a full rewrite instead of a changelog.
PDF_V1 = "SECTION 1\nA court may consider the tax consequences.\nSECTION 2\nEffective July 1."
PDF_V2 = "SECTION 1\nA court shall consider the tax consequences.\nSECTION 2\nEffective July 1."
XML_V1 = "Section 1\nA court may consider the tax consequences.\nSection 2\nEffective July 1."
XML_V2 = "Section 1\nA court shall consider the tax consequences.\nSection 2\nEffective July 1."


def _archive(bill, texts_by_url):
    """Run the real archive walk with fetching, S3 and disk writes stubbed out."""

    def fake_fetch_bytes(url):
        return texts_by_url[url].encode("utf-8")

    def fake_extract_func(metadata):
        return lambda data, meta: data.decode("utf-8")

    with mock.patch(
        "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch_bytes
    ), mock.patch(
        "openstates.cli.text_extract.get_extract_func", side_effect=fake_extract_func
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


def _docs(bill):
    return {
        (d.version_note, d.media_type): d
        for d in BillVersionDocument.objects.filter(bill=bill)
    }


def _hunks(diff):
    return re.findall(r"@@[^@]*@@", diff or "")


@pytest.mark.django_db
class TestPerMediaBaseline:
    def test_each_media_type_diffs_against_its_own_previous_rendering(self):
        """The defect. A single XML-preferred baseline meant a version's PDF was compared
        against the previous version's XML — measured live on the 2026-08-30 US archive run as
        19 of 21 PDF diffs collapsing to a whole-document hunk while every XML diff was fine."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v1.links.create(url="https://x.test/v1.xml", media_type="text/xml")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")
        v2.links.create(url="https://x.test/v2.xml", media_type="text/xml")

        _archive(
            bill,
            {
                "https://x.test/v1.pdf": PDF_V1,
                "https://x.test/v1.xml": XML_V1,
                "https://x.test/v2.pdf": PDF_V2,
                "https://x.test/v2.xml": XML_V2,
            },
        )

        docs = _docs(bill)
        for media in ("application/pdf", "text/xml"):
            diff = docs[("Enrolled", media)].diff_from_previous_version
            assert len(_hunks(diff)) == 1, f"{media}: expected one hunk, got {_hunks(diff)}"
            assert "-A court may consider the tax consequences." in diff
            assert "+A court shall consider the tax consequences." in diff
            # The giveaway of a cross-media comparison: the unchanged headings differ in case
            # between the two renderings, so they would appear as changes.
            assert not ("SECTION 2" in diff and "Section 2" in diff)

    def test_a_versions_first_appearance_of_a_media_type_has_no_diff(self):
        """A rendering with no previous counterpart has nothing to compare against, and must
        record that rather than reaching for a different media type's text."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")
        v2.links.create(url="https://x.test/v2.xml", media_type="text/xml")

        _archive(
            bill,
            {
                "https://x.test/v1.pdf": PDF_V1,
                "https://x.test/v2.pdf": PDF_V2,
                "https://x.test/v2.xml": XML_V2,
            },
        )

        docs = _docs(bill)
        assert docs[("Enrolled", "text/xml")].diff_from_previous_version is None
        assert docs[("Enrolled", "application/pdf")].diff_from_previous_version is not None

    def test_a_missing_rendering_does_not_break_the_lineage(self):
        """A version that ships PDF-only must not orphan the XML chain: version 3's XML should
        diff against version 1's, not be treated as brand new."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.xml", media_type="text/xml")
        v2 = bill.versions.create(note="Substitute #1", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")
        v3 = bill.versions.create(note="Enrolled", date="")
        v3.links.create(url="https://x.test/v3.xml", media_type="text/xml")

        _archive(
            bill,
            {
                "https://x.test/v1.xml": XML_V1,
                "https://x.test/v2.pdf": PDF_V1,
                "https://x.test/v3.xml": XML_V2,
            },
        )

        diff = _docs(bill)[("Enrolled", "text/xml")].diff_from_previous_version
        assert diff is not None
        assert "+A court shall consider the tax consequences." in diff

    def test_two_files_of_the_same_version_are_never_diffed_against_each_other(self):
        """Pre-existing invariant, pinned because the baseline bookkeeping moved. Baselines are
        updated once per version, after all its documents, not once per document."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v1.links.create(url="https://x.test/v1.xml", media_type="text/xml")

        _archive(
            bill,
            {"https://x.test/v1.pdf": PDF_V1, "https://x.test/v1.xml": XML_V1},
        )

        docs = _docs(bill)
        assert docs[("Introduced", "application/pdf")].diff_from_previous_version is None
        assert docs[("Introduced", "text/xml")].diff_from_previous_version is None

    def test_an_already_archived_document_still_feeds_its_own_baseline(self):
        """Specific to this path — the recompute path has no skip concept. A partial re-run
        (only a new amendment unarchived) must still diff correctly against the previously
        archived text of the same media type."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        _archive(bill, {"https://x.test/v1.pdf": PDF_V1})
        assert BillVersionDocument.objects.filter(bill=bill).count() == 1

        # Now a second version appears and only it is unarchived.
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")
        counters = _archive(
            bill,
            {"https://x.test/v1.pdf": PDF_V1, "https://x.test/v2.pdf": PDF_V2},
        )

        assert counters["skipped"] == 1
        diff = _docs(bill)[("Enrolled", "application/pdf")].diff_from_previous_version
        assert "+A court shall consider the tax consequences." in diff

    def test_an_unknown_stage_version_neither_takes_nor_becomes_a_baseline(self):
        """Pre-existing OPEN-34 behaviour, pinned because per-media baselines flow through the
        same branch: a version whose note matches no known stage has no reliable position, so it
        gets no diff and does not feed the lineage."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        mystery = bill.versions.create(note="???", date="")
        mystery.links.create(url="https://x.test/mystery.pdf", media_type="application/pdf")

        _archive(
            bill,
            {
                "https://x.test/v1.pdf": PDF_V1,
                "https://x.test/mystery.pdf": "totally unrelated text",
            },
        )

        docs = _docs(bill)
        assert docs[("???", "application/pdf")].diff_from_previous_version is None

    def test_no_whole_document_diff_where_both_renderings_exist(self):
        """States the acceptance condition the way it was measured in production: a newly
        archived version carrying both XML and PDF produces a targeted diff for each, and
        neither is a whole-document replacement.

        Uses renderings that share **no** line with each other, which is the real shape — a PDF
        carries line numbers and hard wrapping that the XML has nowhere. The small fixture above
        happens to leave one line identical across both renderings, so a cross-media diff there
        still keeps a context line and is not technically whole-document. Here every line
        differs, so the pre-OPEN-217 behaviour produces exactly what production showed: one hunk
        from line 1 with nothing surviving."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v1.links.create(url="https://x.test/v1.xml", media_type="text/xml")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")
        v2.links.create(url="https://x.test/v2.xml", media_type="text/xml")

        # No line is shared between the PDF and XML renderings.
        pdf_v1 = "1  SECTION 1  A court may consider\n2  the tax consequences.\n3  Effective."
        pdf_v2 = "1  SECTION 1  A court shall consider\n2  the tax consequences.\n3  Effective."
        xml_v1 = "Section 1\nA court may consider the tax consequences.\nEffective."
        xml_v2 = "Section 1\nA court shall consider the tax consequences.\nEffective."
        _archive(
            bill,
            {
                "https://x.test/v1.pdf": pdf_v1,
                "https://x.test/v1.xml": xml_v1,
                "https://x.test/v2.pdf": pdf_v2,
                "https://x.test/v2.xml": xml_v2,
            },
        )

        docs = _docs(bill)
        for media in ("application/pdf", "text/xml"):
            diff = docs[("Enrolled", media)].diff_from_previous_version
            hunks = _hunks(diff)
            assert len(hunks) == 1
            # a whole-document replacement starts at line 1 on both sides and keeps no context
            assert not (
                re.fullmatch(r"@@ -1(,\d+)? \+1(,\d+)? @@", hunks[0])
                and "\n " not in diff
            ), f"{media}: whole-document replacement, the bug this fixes: {hunks[0]}"

    def test_an_errored_document_does_not_become_a_baseline(self):
        """Review round 1 caught an overclaim: OPEN-217's AC4 lists "error rows never become
        baselines" but no test named it. An error row has no usable text, so it must not poison
        or occupy its media type's lineage -- the next real document of that rendering has
        nothing to compare against, and must say so rather than reaching sideways.

        This is the exact shape of Washington HB 1344 and Michigan SR 123 in production, where
        an errored PDF let the old shared baseline fall through to the sibling HTML."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        v1.links.create(url="https://x.test/v1.xml", media_type="text/xml")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")

        # v1's PDF extracts to nothing -> is_error, so it never becomes the PDF baseline. Its
        # XML sibling does extract, and must NOT be borrowed for v2's PDF.
        _archive(
            bill,
            {
                "https://x.test/v1.pdf": "",
                "https://x.test/v1.xml": XML_V1,
                "https://x.test/v2.pdf": PDF_V2,
            },
        )

        docs = _docs(bill)
        assert docs[("Introduced", "application/pdf")].is_error is True
        assert docs[("Enrolled", "application/pdf")].diff_from_previous_version is None

    def test_an_already_archived_error_row_does_not_become_a_baseline_either(self):
        """The skip path has its own eligibility check, separate from the fetch path's, so
        "error rows are excluded" has to hold on both. Round 1 flagged that only the happy skip
        case was covered."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        v1.links.create(url="https://x.test/v1.pdf", media_type="application/pdf")
        _archive(bill, {"https://x.test/v1.pdf": ""})
        assert BillVersionDocument.objects.get(bill=bill).is_error is True

        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")
        counters = _archive(
            bill, {"https://x.test/v1.pdf": "", "https://x.test/v2.pdf": PDF_V2}
        )

        assert counters["skipped"] == 1
        assert _docs(bill)[("Enrolled", "application/pdf")].diff_from_previous_version is None

    def test_duplicate_same_media_links_pick_a_deterministic_baseline(self):
        """Round 1: 3,744 production versions carry more than one successfully-extracted
        document of the same media type (Virginia 2,201, Utah 633, Arizona 544, Washington 234,
        United States 129). Media type is the lineage key now, so whichever of them becomes the
        baseline propagates into every later comparison -- and `links.all()` has no guaranteed
        row order. Links are walked sorted by (media_type, url), so the winner is stable."""
        bill = _make_bill()
        v1 = bill.versions.create(note="Introduced", date="")
        # created in the reverse of sorted order, so an unsorted walk would pick "b"
        v1.links.create(url="https://x.test/v1-b.pdf", media_type="application/pdf")
        v1.links.create(url="https://x.test/v1-a.pdf", media_type="application/pdf")
        v2 = bill.versions.create(note="Enrolled", date="")
        v2.links.create(url="https://x.test/v2.pdf", media_type="application/pdf")

        _archive(
            bill,
            {
                # "-a" sorts last of the two v1 links, so it is the baseline; give the two
                # distinct text so the resulting diff says which one won.
                "https://x.test/v1-a.pdf": "FROM A\nshared line",
                "https://x.test/v1-b.pdf": "FROM B\nshared line",
                "https://x.test/v2.pdf": "FROM A\nshared line\nplus an addition",
            },
        )

        diff = _docs(bill)[("Enrolled", "application/pdf")].diff_from_previous_version
        # url sort puts v1-a.pdf before v1-b.pdf, and last-write-wins makes v1-b the baseline.
        assert "-FROM B" in diff and "+FROM A" in diff, diff
        assert "+plus an addition" in diff
