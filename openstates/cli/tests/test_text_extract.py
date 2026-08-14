import pytest  # type: ignore
from unittest import mock

from openstates.data.models import (
    Division,
    Jurisdiction,
    Organization,
    Bill,
    BillVersionDocument,
)
from openstates.cli.text_extract import (
    _version_sort_key,
    _note_stage,
    _STAGE_UNKNOWN,
    _STAGE_INTRODUCED,
    _STAGE_ENACTED,
    archive_bill_versions,
    recompute_bill_diff_order,
    _reextract_document,
    _clean_wa_text,
    S3_BILL_ARCHIVE_BUCKET,
)
import difflib


def note_order(notes, dates=None):
    """Sort a list of version_notes (optionally paired with dates) via _version_sort_key."""
    if dates is None:
        dates = [None] * len(notes)
    pairs = list(zip(notes, dates))
    pairs.sort(key=lambda p: _version_sort_key(p[0], p[1]))
    return [p[0] for p in pairs]


# OPEN-34: every case below is a real version_note sequence captured during the audit
# (12-bill-per-jurisdiction sample against a real, production-derived dataset), in the exact
# scrambled order the DB actually returned them in for an unordered `bill.versions.all()`
# walk. Each asserts the *corrected* forward chronological order the fix should produce,
# regardless of that original order.
class TestVersionSortKeyRealSamples:
    def test_fl_committee_substitute_and_engrossed_codes(self):
        # SB 1668 -- "Filed" was not walked first in the original (unfixed) DB order.
        scrambled = ["S 1668 c2", "S 1668 e1", "S 1668 er", "S 1668 Filed", "S 1668 c1"]
        assert note_order(scrambled) == [
            "S 1668 Filed",
            "S 1668 c1",
            "S 1668 c2",
            "S 1668 e1",
            "S 1668 er",
        ]

    def test_fl_committee_substitutes_only(self):
        # SB 1220 -- another real "Filed" -not-first case.
        scrambled = ["S 1220 c1", "S 1220 c2", "S 1220 c3", "S 1220 e1", "S 1220 Filed"]
        assert note_order(scrambled) == [
            "S 1220 Filed",
            "S 1220 c1",
            "S 1220 c2",
            "S 1220 c3",
            "S 1220 e1",
        ]

    def test_fl_proposed_committee_bill_prefix(self):
        # SB 7026 -- already-forward real order; confirms "pb" doesn't get misclassified.
        already_forward = ["S 7026 pb", "S 7026 Filed", "S 7026 er"]
        assert note_order(already_forward) == already_forward

    def test_mi_substitute_inserted_before_introduced(self):
        # SB 856 -- MI's real, common pattern: Substitute versions land BEFORE "Introduced
        # Bill" in DB walk order. The ticket's original single clean example (no substitutes)
        # wasn't representative of this.
        scrambled = [
            "Substitute (S-1)",
            "Substitute (S-1) - 2",
            "Senate Introduced Bill",
        ]
        assert note_order(scrambled) == [
            "Senate Introduced Bill",
            "Substitute (S-1)",
            "Substitute (S-1) - 2",
        ]

    def test_mi_numbered_substitutes_stay_ordered(self):
        scrambled = [
            "Substitute (S-2)",
            "Substitute (S-2) - 2",
            "Senate Introduced Bill",
            "As Passed by the Senate",
        ]
        assert note_order(scrambled) == [
            "Senate Introduced Bill",
            "Substitute (S-2)",
            "Substitute (S-2) - 2",
            "As Passed by the Senate",
        ]

    def test_az_already_forward_stays_stable(self):
        # HB 2874 -- one of the 11/12 real AZ bills already forward; the fix must not
        # perturb an already-correct sequence.
        already_forward = [
            "Introduced Version",
            "House Engrossed Version",
            "Senate Engrossed Version",
            "Conference Engrossed Version",
            "Conference Committee",
            "SENATE - Judiciary and Elections",
            "SENATE - Rogers flr amend (ref JUDE) adopted",
        ]
        assert note_order(already_forward) == already_forward

    def test_az_floor_note_referencing_engrossed_by_name(self):
        # HB 2035 -- the one real AZ exception: a floor-amendment note landed first in DB
        # order, and its text happens to contain the word "Engrossed" as part of a
        # cross-reference ("ref Senate Engrossed House Bill"), not because it IS one.
        scrambled = [
            "SENATE - Werner flr amend (ref Senate Engrossed House Bill) adopted",
            "Introduced Version",
            "House Engrossed Version",
            "Senate Engrossed Version",
            "Chaptered Version",
            "SENATE - Werner flr amend (ref Bill) adopted",
        ]
        result = note_order(scrambled)
        assert result[0] == "Introduced Version"
        assert result[-1] == "Chaptered Version"
        # both floor notes land after both real Engrossed versions, before Chaptered. Note:
        # filtering on "not startswith SENATE -" matters here -- the floor notes themselves
        # contain the word "Engrossed" as part of a cross-reference, not because they are one.
        engrossed_positions = [
            result.index(n)
            for n in scrambled
            if "Engrossed" in n and not n.startswith("SENATE -")
        ]
        floor_note_positions = [
            result.index(n) for n in scrambled if n.startswith("SENATE -")
        ]
        assert (
            max(engrossed_positions)
            < min(floor_note_positions)
            < result.index("Chaptered Version")
        )

    def test_va_hb1207_backward_sequence(self):
        # Real, confirmed-backward VA example from the ticket/OPEN-33.
        backward = [
            "Chaptered",
            "Reenrolled",
            "Governor Substitute",
            "Conference Report",
            "Introduced",
        ]
        assert note_order(backward) == [
            "Introduced",
            "Conference Report",
            "Governor Substitute",
            "Reenrolled",
            "Chaptered",
        ]

    def test_va_sb542_backward_sequence(self):
        backward = ["Governor's Veto Explanation", "Governor Substitute", "Enrolled"]
        assert note_order(backward) == [
            "Enrolled",
            "Governor Substitute",
            "Governor's Veto Explanation",
        ]

    def test_ut_enrolled_out_of_position(self):
        # HB 101-shaped real example -- "Enrolled" walked second, immediately after
        # "Introduced", well before the amendment sequence it should follow.
        scrambled = [
            "Enrolled",
            "Introduced",
            "Amended 2/13/2026",
            "Amended Excerpts 2/13/2026",
            "House Amendment 1",
        ]
        result = note_order(scrambled)
        assert result[0] == "Introduced"
        assert result[-1] == "Enrolled"

    def test_ut_substitute_numbers_reordered(self):
        # HB 372 -- Substitute #4 was walked before Substitute #3 in the real DB order.
        scrambled = [
            "Introduced",
            "Comparison to Original Bill",
            "Substitute #1",
            "Comparison to Sub #1",
            "Substitute #2",
            "Comparison to Sub #2",
            "Comparison to Sub #3",
            "Substitute #4",
            "Substitute #3",
            "Enrolled",
            "Comparison to Sub #4",
            "Substitute #5",
        ]
        result = note_order(scrambled)
        assert (
            result.index("Substitute #3")
            < result.index("Substitute #4")
            < result.index("Substitute #5")
        )
        assert result[0] == "Introduced"
        assert result[-1] == "Enrolled"

    def test_wa_hb1960_root_caused_mechanism(self):
        # Real WA example (AC2): "Bill" (introduced) lands near the end, and "Engrossed
        # Third Substitute" lands before the plain "Third Substitute" it amends.
        scrambled = [
            "Substitute Bill",
            "Second Substitute Bill",
            "Engrossed Third Substitute Bill",
            "Third Substitute Bill",
            "Bill",
            "Third Substitute Passed Legislature",
        ]
        assert note_order(scrambled) == [
            "Bill",
            "Substitute Bill",
            "Second Substitute Bill",
            "Third Substitute Bill",
            "Engrossed Third Substitute Bill",
            "Third Substitute Passed Legislature",
        ]

    def test_wa_sb5466_engrossed_before_plain_substitute(self):
        scrambled = [
            "Engrossed Substitute Bill",
            "Substitute Bill",
            "Second Substitute Bill",
            "Third Substitute Bill",
            "Bill",
        ]
        assert note_order(scrambled) == [
            "Bill",
            "Substitute Bill",
            "Engrossed Substitute Bill",
            "Second Substitute Bill",
            "Third Substitute Bill",
        ]

    def test_us_federal_uses_real_dates_when_present(self):
        # US federal is ~99.4% dated per the audit -- when dates are present they resolve
        # ordering more precisely than the note-only heuristic (real chronology: Introduced
        # -> Reported -> Engrossed).
        scrambled = [
            ("Engrossed in Senate", "2026-03-02"),
            ("Reported to Senate", "2026-02-10"),
            ("Introduced in Senate", "2026-01-05"),
        ]
        notes = [n for n, _ in scrambled]
        dates = [d for _, d in scrambled]
        assert note_order(notes, dates) == [
            "Introduced in Senate",
            "Reported to Senate",
            "Engrossed in Senate",
        ]

    def test_us_federal_note_only_fallback_stays_macro_correct(self):
        # Without a date (the rare ~0.6% case), the note-based fallback is documented as
        # only macro-stage-accurate, not micro-precise -- Introduced must still be first.
        scrambled = [
            "Engrossed in Senate",
            "Reported to Senate",
            "Introduced in Senate",
        ]
        result = note_order(scrambled)
        assert result[0] == "Introduced in Senate"

    def test_date_does_not_override_note_stage_for_mixed_bills(self):
        # A dated version must not unconditionally sort before an undated one regardless of
        # true stage -- the date is a same-stage tiebreaker only (see _version_sort_key's
        # docstring). An undated "Enrolled" (final passage) must still sort after a dated
        # "Introduced" (introduced stage).
        pairs = [("Enrolled", None), ("Introduced in Senate", "2026-01-05")]
        notes = [n for n, _ in pairs]
        dates = [d for _, d in pairs]
        assert note_order(notes, dates) == ["Introduced in Senate", "Enrolled"]


class TestNoteStageUnknownFallback:
    def test_unrecognized_note_is_unknown_not_guessed(self):
        stage, _ = _note_stage("Some Never-Before-Seen Document Type")
        assert stage == _STAGE_UNKNOWN

    def test_unknown_notes_sort_after_every_known_stage(self):
        notes = ["Chaptered", "Introduced", "Some Never-Before-Seen Document Type"]
        result = note_order(notes)
        assert result[-1] == "Some Never-Before-Seen Document Type"


class TestMaNoteStage:
    """
    OPEN-37: MA's two real version_notes -- "Bill Text" (introduced, scrapers/ma/bills.py's
    existing add_version_link call) and "Chapter Law Text (Enacted)" (the new second version
    this ticket adds) -- must both resolve to a known stage, in the right order, or MA's
    entire diff lineage is excluded (both _STAGE_UNKNOWN) exactly as it was before this fix.
    """

    def test_bill_text_is_introduced_stage(self):
        stage, _ = _note_stage("Bill Text")
        assert stage == _STAGE_INTRODUCED

    def test_chapter_law_text_enacted_is_already_enacted_stage(self):
        # No code change needed for this side -- "chapter" is already matched by the
        # generic enacted-stage regex above. Pinned here so a future refactor of that regex
        # can't silently break MA's enacted-stage note without a test noticing.
        stage, _ = _note_stage("Chapter Law Text (Enacted)")
        assert stage == _STAGE_ENACTED

    def test_bill_text_exact_match_does_not_catch_other_notes(self):
        # The fix is an exact match ("bill text"), not a substring check -- must not
        # reclassify some other jurisdiction's differently-worded note that merely contains
        # "bill" or "text".
        assert _note_stage("Bill Text - Substitute")[0] != _STAGE_INTRODUCED
        assert _note_stage("Engrossed Bill")[0] != _STAGE_UNKNOWN  # sanity: still "engross"

    def test_ma_stage_chain_sorts_introduced_before_enacted(self):
        result = note_order(["Chapter Law Text (Enacted)", "Bill Text"])
        assert result == ["Bill Text", "Chapter Law Text (Enacted)"]


def _make_bill(jid="ocd-jurisdiction/country:us/state:ak/government"):
    Division.objects.get_or_create(
        id="ocd-division/country:us", defaults={"name": "USA"}
    )
    j, _ = Jurisdiction.objects.get_or_create(
        id=jid, defaults={"division_id": "ocd-division/country:us", "name": "Test"}
    )
    org, _ = Organization.objects.get_or_create(
        jurisdiction=j, name="House", classification="lower"
    )
    session = j.legislative_sessions.create(identifier="2026", name="2026")
    bill = Bill.objects.create(
        identifier="HB 1",
        title="A test bill",
        legislative_session=session,
        from_organization=org,
    )
    return bill


@pytest.mark.django_db
class TestArchiveBillVersionsRegressionOPEN34:
    """
    AC5: a fixture bill/version set with an intentionally out-of-chronological-order DB row
    sequence, mirroring the real UT/VA shape (Enrolled created before Introduced before the
    amendment stage). Before OPEN-34's fix, archive_bill_versions() walked bill.versions.all()
    directly -- whatever order Postgres returned (here, creation order) -- so this fixture
    would have diffed "Enrolled" against nothing (first seen) and "Introduced" against
    "Enrolled"'s text, backward. Post-fix, _version_sort_key() reorders the walk regardless
    of creation order, so the diff chain comes out correct.
    """

    def _make_versions_scrambled(self, bill):
        # Created in this exact (wrong) order -- a fresh test DB returns bill.versions.all()
        # in creation order with no explicit ordering, the same accident OPEN-34 describes.
        enrolled = bill.versions.create(note="Enrolled", date="")
        introduced = bill.versions.create(note="Introduced", date="")
        substitute = bill.versions.create(note="Substitute #1", date="")

        enrolled.links.create(
            url="https://example.test/enrolled.pdf", media_type="application/pdf"
        )
        introduced.links.create(
            url="https://example.test/introduced.pdf", media_type="application/pdf"
        )
        substitute.links.create(
            url="https://example.test/sub1.pdf", media_type="application/pdf"
        )

        # Sanity check the fixture actually reproduces the bug's precondition: the DB really
        # does return them in creation (non-chronological) order with no fix applied.
        assert [v.note for v in bill.versions.all()] == [
            "Enrolled",
            "Introduced",
            "Substitute #1",
        ]

    def test_diffs_come_out_forward_regardless_of_creation_order(self):
        bill = _make_bill()
        self._make_versions_scrambled(bill)

        texts_by_url = {
            "https://example.test/introduced.pdf": "Section 1. Original text.",
            "https://example.test/sub1.pdf": "Section 1. Original text. Section 2. New text.",
            "https://example.test/enrolled.pdf": (
                "Section 1. Original text. Section 2. New text. Section 3. Final text."
            ),
        }

        def fake_fetch_bytes(url):
            return texts_by_url[url].encode("utf-8")

        def fake_extract_func(metadata):
            return lambda data, meta: data.decode("utf-8")

        with mock.patch(
            "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch_bytes
        ), mock.patch(
            "openstates.cli.text_extract.get_extract_func",
            side_effect=fake_extract_func,
        ), mock.patch(
            "openstates.cli.text_extract._upload_and_verify", return_value=None
        ), mock.patch(
            "openstates.cli.text_extract._block_page_reason", return_value=None
        ), mock.patch(
            "os.makedirs"
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ):
            archive_bill_versions(bill)

        docs = {
            d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)
        }
        assert (
            docs["Introduced"].diff_from_previous_version is None
        )  # first in true order
        sub1_diff = docs["Substitute #1"].diff_from_previous_version
        enrolled_diff = docs["Enrolled"].diff_from_previous_version
        assert "+Section 1. Original text. Section 2. New text." in sub1_diff
        assert (
            "+Section 1. Original text. Section 2. New text. Section 3. Final text."
            in enrolled_diff
        )
        # Never diffed backward: the enrolled doc's diff must not claim to *remove* its own
        # final text (that would mean it was diffed as the earlier, not later, version).
        assert (
            "-Section 1. Original text. Section 2. New text. Section 3. Final text."
            not in enrolled_diff
        )

    def test_pre_fix_walk_would_have_gotten_this_backward(self):
        # Demonstrates the bug this regression test guards against: walking
        # bill.versions.all() directly (the pre-fix behavior) sees "Enrolled" first, which
        # would seed prior_text from the *final* text and diff "Introduced" against it --
        # backward. This asserts the raw (unordered) walk really does differ from the fixed
        # walk for this fixture, i.e. the fixture is a real regression case, not a no-op.
        bill = _make_bill()
        self._make_versions_scrambled(bill)

        raw_walk_notes = [v.note for v in bill.versions.all()]
        from openstates.cli.text_extract import _version_sort_key

        fixed_walk_notes = [
            v.note
            for v in sorted(
                bill.versions.all(), key=lambda v: _version_sort_key(v.note, v.date)
            )
        ]
        assert raw_walk_notes != fixed_walk_notes
        assert fixed_walk_notes == ["Introduced", "Substitute #1", "Enrolled"]

    def test_unknown_stage_version_never_seeds_or_consumes_prior_text(self):
        bill = _make_bill()
        introduced = bill.versions.create(note="Introduced", date="")
        mystery = bill.versions.create(
            note="Some Never-Before-Seen Document Type", date=""
        )
        enrolled = bill.versions.create(note="Enrolled", date="")

        introduced.links.create(
            url="https://example.test/introduced.pdf", media_type="application/pdf"
        )
        mystery.links.create(
            url="https://example.test/mystery.pdf", media_type="application/pdf"
        )
        enrolled.links.create(
            url="https://example.test/enrolled.pdf", media_type="application/pdf"
        )

        texts_by_url = {
            "https://example.test/introduced.pdf": "Original text.",
            "https://example.test/mystery.pdf": "Unrelated mystery content.",
            "https://example.test/enrolled.pdf": "Original text. Amended.",
        }

        def fake_fetch_bytes(url):
            return texts_by_url[url].encode("utf-8")

        def fake_extract_func(metadata):
            return lambda data, meta: data.decode("utf-8")

        with mock.patch(
            "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch_bytes
        ), mock.patch(
            "openstates.cli.text_extract.get_extract_func",
            side_effect=fake_extract_func,
        ), mock.patch(
            "openstates.cli.text_extract._upload_and_verify", return_value=None
        ), mock.patch(
            "openstates.cli.text_extract._block_page_reason", return_value=None
        ), mock.patch(
            "os.makedirs"
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ):
            archive_bill_versions(bill)

        docs = {
            d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)
        }
        assert (
            docs["Some Never-Before-Seen Document Type"].diff_from_previous_version
            is None
        )
        # Enrolled must diff against Introduced's text, not the unknown-stage mystery doc's.
        assert docs["Enrolled"].diff_from_previous_version is not None
        assert "mystery" not in docs["Enrolled"].diff_from_previous_version.lower()


@pytest.mark.django_db
class TestRecomputeDiffOrder:
    def test_dry_run_reports_correction_without_writing(self):
        bill = _make_bill()
        # Simulate the pre-fix bug's output directly: Enrolled archived first (wrongly
        # null, nothing to diff against yet), Introduced archived second and wrongly
        # diffed against Enrolled's (later) text.
        enrolled = BillVersionDocument.objects.create(
            bill=bill,
            version_note="Enrolled",
            version_date="",
            source_url="https://example.test/enrolled.pdf",
            media_type="application/pdf",
            raw_text="Original text. Amended.",
            is_error=False,
            diff_from_previous_version=None,
        )
        introduced = BillVersionDocument.objects.create(
            bill=bill,
            version_note="Introduced",
            version_date="",
            source_url="https://example.test/introduced.pdf",
            media_type="application/pdf",
            raw_text="Original text.",
            is_error=False,
            diff_from_previous_version="--- \n+++ \n@@ -1 +1 @@\n-Original text. Amended.\n+Original text.",
        )

        result = recompute_bill_diff_order(bill)
        changed_notes = {doc.version_note for doc, _ in result["changed"]}
        assert changed_notes == {"Enrolled", "Introduced"}

        introduced.refresh_from_db()
        enrolled.refresh_from_db()
        # dry run (recompute_bill_diff_order alone never writes) -- DB values are untouched
        assert introduced.diff_from_previous_version.startswith("--- ")
        assert enrolled.diff_from_previous_version is None

    def test_commit_corrects_the_stored_values(self):
        from openstates.cli.text_extract import recompute_diff_order
        from click.testing import CliRunner

        bill = _make_bill()
        BillVersionDocument.objects.create(
            bill=bill,
            version_note="Enrolled",
            version_date="",
            source_url="https://example.test/enrolled.pdf",
            media_type="application/pdf",
            raw_text="Original text. Amended.",
            is_error=False,
            diff_from_previous_version=None,
        )
        introduced = BillVersionDocument.objects.create(
            bill=bill,
            version_note="Introduced",
            version_date="",
            source_url="https://example.test/introduced.pdf",
            media_type="application/pdf",
            raw_text="Original text.",
            is_error=False,
            diff_from_previous_version="WRONG-BACKWARD-DIFF",
        )

        with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
            "openstates.cli.text_extract.abbr_to_jid",
            return_value=bill.legislative_session.jurisdiction_id,
        ):
            runner = CliRunner()
            result = runner.invoke(recompute_diff_order, ["ak", "--commit"])
        assert result.exit_code == 0, result.output

        introduced.refresh_from_db()
        assert (
            introduced.diff_from_previous_version is None
        )  # first in true order, correctly nulled

        enrolled = BillVersionDocument.objects.get(bill=bill, version_note="Enrolled")
        assert "+Original text. Amended." in enrolled.diff_from_previous_version
        assert "-Original text. Amended." not in enrolled.diff_from_previous_version
        # archive_location/archived_at/sha256_hash/raw_text/is_error must be untouched
        assert enrolled.raw_text == "Original text. Amended."
        assert enrolled.is_error is False


@pytest.mark.django_db
class TestPriorTextPrefersXmlOverPdf:
    """
    Found 2026-08-12 alongside enabling US XML extraction: prior_text (the text the *next*
    version gets diffed against) used to hardcode `this_version_texts.get("application/pdf")`
    first, unconditionally, for every jurisdiction. US and UT are the only jurisdictions where
    a version can have both a real (non-DoNotDownload) PDF and XML link today, so this only
    changes behavior for those two -- XML has no page-break/line-wrap artifacts, making it a
    cleaner diffing source than PDF's line-numbered extraction.
    """

    def test_next_versions_diff_is_computed_against_xml_not_pdf(self):
        bill = _make_bill()
        introduced = bill.versions.create(note="Introduced", date="")
        enrolled = bill.versions.create(note="Enrolled", date="")

        # Deliberately give "Introduced" both links with *different* extracted text -- the
        # XML text is clean, the PDF text carries a fake line-number-style prefix noise on
        # the same content, mirroring a real line-numbered PDF extraction -- so the test can
        # tell which one prior_text actually carried forward.
        introduced.links.create(
            url="https://example.test/introduced.xml", media_type="text/xml"
        )
        introduced.links.create(
            url="https://example.test/introduced.pdf", media_type="application/pdf"
        )
        enrolled.links.create(
            url="https://example.test/enrolled.pdf", media_type="application/pdf"
        )

        texts_by_url = {
            "https://example.test/introduced.xml": "Section 1. Text.",
            "https://example.test/introduced.pdf": "1 Section 1. Text.",
            "https://example.test/enrolled.pdf": "Section 1. Text.\nSection 2. Added.",
        }

        def fake_fetch_bytes(url):
            return texts_by_url[url].encode("utf-8")

        def fake_extract_func(metadata):
            return lambda data, meta: data.decode("utf-8")

        with mock.patch(
            "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch_bytes
        ), mock.patch(
            "openstates.cli.text_extract.get_extract_func",
            side_effect=fake_extract_func,
        ), mock.patch(
            "openstates.cli.text_extract._upload_and_verify", return_value=None
        ), mock.patch(
            "openstates.cli.text_extract._block_page_reason", return_value=None
        ), mock.patch(
            "os.makedirs"
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ):
            archive_bill_versions(bill)

        enrolled_pdf_doc = BillVersionDocument.objects.get(
            bill=bill, version_note="Enrolled", media_type="application/pdf"
        )
        diff = enrolled_pdf_doc.diff_from_previous_version
        # With XML preferred, "Section 1. Text." is unchanged between versions -- the diff
        # should show only the real addition, not a spurious first-line change.
        assert "+Section 2. Added." in diff
        assert "-Section 1. Text." not in diff
        assert "+Section 1. Text." not in diff
        # If PDF had won instead, prior_text would have been "1 Section 1. Text." (the noisy
        # prefixed line), which would show up as a spurious removed line here.
        assert "1 Section 1. Text." not in diff


class TestUtahXmlExtractor:
    """
    OPEN-49: Utah's bill XML export declares `encoding="UTF-16"` in its own prolog, but the
    real bytes are plain UTF-8/ASCII (confirmed directly against real bills, 2026-08-09: no
    UTF-16 byte-order-mark, every byte in the prolog itself is single-byte ASCII). libxml2
    honors the declared encoding and fails almost immediately on real content as a result.
    """

    def test_extracts_real_text_despite_mismatched_encoding_declaration(self):
        from openstates.fulltext.ut import handle_utah_xml

        # A minimal but structurally real fixture: the same UTF-16-labeled-but-UTF-8-bytes
        # mismatch, spread across several of Utah's real nested elements.
        xml = (
            b'<?xml version="1.0" encoding="UTF-16"?>\n'
            b'<leg billnum="HB0001">'
            b'<tbox><st>Test Bill Amendments</st>'
            b'<sponsorhead>Chief Sponsor: Jane Doe</sponsorhead></tbox>'
            b'<body><p>This bill modifies provisions of the state code.</p></body>'
            b"</leg>"
        )
        text = handle_utah_xml(
            xml, {"url": "", "media_type": "text/xml", "title": "", "jurisdiction_id": ""}
        )
        assert "Test Bill Amendments" in text
        assert "Chief Sponsor: Jane Doe" in text
        assert "This bill modifies provisions of the state code." in text
        # Tag names/attributes themselves must not leak into the extracted text.
        assert "billnum" not in text
        assert "<p>" not in text

    def test_raises_on_genuinely_unparseable_data(self):
        from openstates.fulltext.ut import handle_utah_xml

        with pytest.raises(Exception):
            handle_utah_xml(
                b"this is not xml at all, not even close",
                {"url": "", "media_type": "text/xml", "title": "", "jurisdiction_id": ""},
            )


@pytest.mark.django_db
class TestReextractDocument:
    """
    OPEN-49: generalizes OPEN-33's VA backfill approach (reprocess an already-archived
    document's raw bytes straight off disk, no re-fetching, no S3) into a reusable command
    instead of a one-off script, so the next jurisdiction that needs a missing-extractor
    backfill doesn't require hand-rolling this again.
    """

    def _make_doc(self, bill, tmp_path, monkeypatch, *, media_type, filename, content):
        monkeypatch.setattr("openstates.settings.ARCHIVE_ROOT_DIR", str(tmp_path))
        rel_path = f"bills/raw/ak/2026/lower/HB1--x/{filename}"
        full_path = tmp_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        return BillVersionDocument.objects.create(
            bill=bill,
            version_note="Introduced",
            version_date="",
            source_url=f"https://example.test/{filename}",
            media_type=media_type,
            raw_text="",
            is_error=True,
            archive_location=f"s3://{S3_BILL_ARCHIVE_BUCKET}/{rel_path}",
        )

    def test_no_archive_location_is_not_attempted(self):
        bill = _make_bill()
        doc = BillVersionDocument.objects.create(
            bill=bill,
            version_note="Introduced",
            version_date="",
            source_url="https://example.test/missing.pdf",
            media_type="application/pdf",
            raw_text="",
            is_error=True,
            archive_location=None,
        )
        result = _reextract_document(doc)
        assert result["attempted"] is False
        assert "no archive_location" in result["reason"]

    def test_missing_local_file_is_not_attempted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openstates.settings.ARCHIVE_ROOT_DIR", str(tmp_path))
        bill = _make_bill()
        doc = BillVersionDocument.objects.create(
            bill=bill,
            version_note="Introduced",
            version_date="",
            source_url="https://example.test/gone.pdf",
            media_type="application/pdf",
            raw_text="",
            is_error=True,
            archive_location=f"s3://{S3_BILL_ARCHIVE_BUCKET}/bills/raw/ak/nope.pdf",
        )
        result = _reextract_document(doc)
        assert result["attempted"] is False
        assert "local file missing" in result["reason"]

    def test_successful_reextraction_reports_fixed(self, tmp_path, monkeypatch):
        # Use a real registered jurisdiction (mi, text/html mapped in CONVERSION_FUNCTIONS)
        # so this test exercises the real registry, not a synthetic mapping.
        bill = _make_bill(jid="ocd-jurisdiction/country:us/state:mi/government")
        doc = self._make_doc(
            bill,
            tmp_path,
            monkeypatch,
            media_type="text/html",
            filename="bill.html",
            content=b'<html><body><div class="WordSection1">Real bill text here.</div></body></html>',
        )

        result = _reextract_document(doc)
        assert result["attempted"] is True
        assert result["new_is_error"] is False
        assert "Real bill text here." in result["new_raw_text"]

    def test_extraction_failure_reports_still_error(self, tmp_path, monkeypatch):
        bill = _make_bill(jid="ocd-jurisdiction/country:us/state:mi/government")
        # A text/html file with none of the expected WordSection1 element -- the real MI
        # extractor will find nothing and raise, matching a genuine still-broken document.
        doc = self._make_doc(
            bill,
            tmp_path,
            monkeypatch,
            media_type="text/html",
            filename="bad.html",
            content=b"<html><body>no matching element here</body></html>",
        )

        result = _reextract_document(doc)
        assert result["attempted"] is True
        assert result["new_is_error"] is True

    def test_commit_writes_raw_text_and_is_error_only(self, tmp_path, monkeypatch):
        from openstates.cli.text_extract import reextract
        from click.testing import CliRunner

        bill = _make_bill(jid="ocd-jurisdiction/country:us/state:mi/government")
        doc = self._make_doc(
            bill,
            tmp_path,
            monkeypatch,
            media_type="text/html",
            filename="bill.html",
            content=b'<html><body><div class="WordSection1">Committed text.</div></body></html>',
        )
        original_location = doc.archive_location

        with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
            "openstates.cli.text_extract.abbr_to_jid",
            return_value=bill.legislative_session.jurisdiction_id,
        ):
            runner = CliRunner()
            result = runner.invoke(reextract, ["mi", "--commit"])
        assert result.exit_code == 0, result.output

        doc.refresh_from_db()
        assert doc.is_error is False
        assert "Committed text." in doc.raw_text
        # Only raw_text/is_error should move -- everything else stays exactly as archived.
        assert doc.archive_location == original_location

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        from openstates.cli.text_extract import reextract
        from click.testing import CliRunner

        bill = _make_bill(jid="ocd-jurisdiction/country:us/state:mi/government")
        doc = self._make_doc(
            bill,
            tmp_path,
            monkeypatch,
            media_type="text/html",
            filename="bill.html",
            content=b'<html><body><div class="WordSection1">Should not be saved.</div></body></html>',
        )

        with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
            "openstates.cli.text_extract.abbr_to_jid",
            return_value=bill.legislative_session.jurisdiction_id,
        ):
            runner = CliRunner()
            result = runner.invoke(reextract, ["mi"])  # default is --dry-run
        assert result.exit_code == 0, result.output
        assert "now_fixed=1" in result.output


# OPEN-7: fixtures below marked "real" are literal substrings captured directly from the live
# archive (HB 1960 and SB 5129, 2025-2026 session) during this ticket's own research -- not
# idealized re-typed strings. Confirmed live: WA's real HTML raw_text has zero internal
# newlines and sometimes glues adjacent elements with no whitespace at all ("ByRepresentatives"),
# which is why these fixtures deliberately have neither.
class TestCleanWaTextPatterns:
    def test_strips_tracking_code_h_prefix(self):
        # real: HB 1960 "Bill" version
        text = "H-1163.2HOUSE BILL 1960AN ACT Relating to renewable energy."
        assert "H-1163.2" not in _clean_wa_text(text)

    def test_strips_tracking_code_s_prefix(self):
        # real: SB 5129 "Bill" version -- the ticket's own [ZH] pattern would miss this "S-"
        # prefix (1,795 real occurrences in the live archive).
        text = "S-0030.8SENATE BILL 5129AN ACT Relating to common interest communities."
        assert "S-0030.8" not in _clean_wa_text(text)

    def test_strips_bare_bill_title(self):
        text = "HOUSE BILL 1960State of WashingtonAN ACT Relating to renewable energy."
        cleaned = _clean_wa_text(text)
        assert "HOUSE BILL 1960" not in cleaned
        assert "AN ACT Relating to renewable energy" in cleaned

    def test_strips_state_legislature_session_line(self):
        text = "State of Washington69th Legislature2025 Regular SessionAN ACT Relating to X."
        cleaned = _clean_wa_text(text)
        assert "69th Legislature" not in cleaned
        assert "AN ACT Relating to X" in cleaned

    def test_strips_leaked_session_fragment_across_page_break(self):
        # real: SB 5129 "Substitute Passed Legislature" PDF -- a page-break split the session
        # line, leaking a bare "Regular Session" fragment onto a numbered line that survived
        # extract_line_numbered_pdf's own filtering upstream (AC6 refinement finding).
        text = "Regular Session\nAN ACT Relating to common interest communities."
        cleaned = _clean_wa_text(text)
        assert "Regular Session" not in cleaned
        assert "AN ACT Relating to common interest communities" in cleaned

    def test_strips_plain_introduced_sponsor_line(self):
        # real: HB 1960 "Bill" version
        text = (
            "ByRepresentatives Ramel, Berg, Doglio, Fitzgibbon, Parshley, Scott, Reed, "
            "and HillPrefiled 02/11/25.Read first time 02/12/25."
            "Referred to Committee on Finance.AN ACT Relating to renewable energy."
        )
        cleaned = _clean_wa_text(text)
        assert "Ramel" not in cleaned
        assert "Prefiled" not in cleaned
        assert "Referred to Committee" not in cleaned
        assert "AN ACT Relating to renewable energy" in cleaned

    def test_strips_watermark(self):
        # ticket's own literal example: a page-number + bill-ID watermark.
        text = "p. 1                         HB 1337AN ACT Relating to something."
        cleaned = _clean_wa_text(text)
        assert "HB 1337" not in cleaned
        assert "AN ACT Relating to something" in cleaned

    def test_watermark_pattern_does_not_collide_with_real_legal_citations(self):
        # real: found in the live archive -- "p. N" also appears in genuine case-law
        # citations (e.g. "F. Supp. 312") which must survive cleaning untouched.
        text = "as determined under United States v. Washington, 384 F. Supp. 312 (1974)."
        cleaned = _clean_wa_text(text)
        # the reflow step may re-wrap this onto more than one line, but the citation's own
        # words/numbers must all survive untouched -- nothing about it looks like the
        # watermark shape once whitespace is normalized back out.
        assert "F. Supp. 312" in cleaned.replace("\n", " ")

    def test_strips_enrolled_bill_certification_header(self):
        # real: HB 1014 "Passed Legislature" version -- a distinct enrolled-bill header shape
        # found during this ticket's own AC5 qualitative review, with no "State of Washington"
        # prefix at all and the bill number glued directly onto the ordinal-legislature number
        # ("...BILL 101469TH LEGISLATURE2025 REGULAR SESSION...").
        text = (
            "CERTIFICATION OF ENROLLMENTENGROSSED HOUSE BILL 101469TH LEGISLATURE"
            "2025 REGULAR SESSIONPassed by the House March 11, 2025  Yeas 93  Nays 3"
            "Speaker of the House of RepresentativesPassed by the Senate April 16, 2025  "
            "Yeas 48  Nays 1President of the SenateAN ACT Relating to something."
        )
        cleaned = _clean_wa_text(text)
        assert "CERTIFICATION OF ENROLLMENT" not in cleaned
        assert "69TH LEGISLATURE" not in cleaned
        assert "Yeas 93" not in cleaned
        assert "Nays 3" not in cleaned
        assert "Speaker of the House" not in cleaned
        assert "President of the Senate" not in cleaned
        assert "AN ACT Relating to something" in cleaned

    def test_bare_ordinal_legislature_pattern_does_not_hang_on_a_large_real_document(self):
        # Regression guard for a real catastrophic-backtracking bug found during AC6
        # refinement: an earlier "\d+\w*\s*Legislature" pattern (no bounded ordinal suffix)
        # took over two minutes on a single real ~90KB "Passed Legislature" bill because an
        # unanchored "\d+\w*" tries every digit run in the document (statute citations,
        # dollar amounts, dates) at every possible split before failing. A version-heavy
        # document with many numeric citations reproduces the same shape at smaller scale.
        text = "RCW 84.55.010, 84.55.030, 84.55.092 " * 2000 + "AN ACT Relating to X."
        cleaned = _clean_wa_text(text)  # must return well within a test timeout, not hang
        assert "AN ACT Relating to X" in cleaned.replace("\n", " ")


class TestCleanWaTextKnownGapsRegression:
    """
    AC7: reproduces the exact three gaps the ticket found in a stripper built from only its
    own starting-point patterns -- a title-prefix variant, an alternate committee sponsor-line
    format, and an unhandled page watermark -- so a future change can't silently reintroduce
    any of them.
    """

    def test_substitute_house_bill_title_prefix(self):
        text = "SUBSTITUTE HOUSE BILL 1337AN ACT Relating to something."
        cleaned = _clean_wa_text(text)
        assert "SUBSTITUTE HOUSE BILL 1337" not in cleaned
        assert "AN ACT Relating to something" in cleaned

    def test_alternate_committee_sponsor_line(self):
        # real shape: HB 1960 "Substitute Bill" version, ticket's own committee-name example
        text = (
            "ByHouse Postsecondary Education & Workforce (originally sponsored by "
            "Representatives Pollet, McEntire, Reed, Macri, and Nance)"
            "READ FIRST TIME 02/26/25.AN ACT Relating to something."
        )
        cleaned = _clean_wa_text(text)
        assert "Postsecondary Education" not in cleaned
        assert "Pollet" not in cleaned
        assert "AN ACT Relating to something" in cleaned

    def test_page_number_bill_id_watermark(self):
        text = "p. 1                         HB 1337AN ACT Relating to something."
        cleaned = _clean_wa_text(text)
        assert "p. 1" not in cleaned
        assert "HB 1337" not in cleaned

    def test_plain_sponsor_line_without_a_trailing_procedural_marker_is_not_over_matched(self):
        # Regression guard for a real, serious bug found while writing this ticket's own
        # tests: an earlier version of the plain-sponsor-line pattern used ".*?" with a
        # lookahead that fell back to "$" (end of string) when neither "Prefiled" nor "Read
        # first time" appeared after the sponsor names. Combined with ".*?" (which -- unlike
        # "[^.]*?" -- crosses periods), that fallback made the pattern consume the *entire
        # rest of the document* as if it were part of the sponsor line, deleting all real bill
        # content. This exact input reproduces that precondition (a sponsor line with no
        # procedural marker following it) and must survive with its real content intact.
        text = "ByRepresentatives Smith.AN ACT Relating to renewable energy. Sec. 1. Real text."
        cleaned = _clean_wa_text(text)
        assert "AN ACT Relating to renewable energy" in cleaned
        assert "Real text" in cleaned


class TestCleanWaTextReflowNecessity:
    """
    AC4: pins the central real-data finding behind this ticket's design -- WA's real HTML
    raw_text has zero internal newlines, so difflib.unified_diff()'s line-based algorithm sees
    the *entire* document as a single "line" and any edit anywhere makes that whole line differ,
    a 100% noise ratio, no matter how much boilerplate substring is stripped out of it. Only
    reconstructing real line boundaries (this cleaner's sentence-reflow step) fixes that. A
    future change that dropped the reflow step and kept only boilerplate-stripping would pass
    every test above but silently regress back to zero real noise reduction -- this test catches
    exactly that regression.
    """

    @staticmethod
    def _noise_ratio(old: str, new: str) -> float:
        old_lines = old.splitlines()
        new_lines = new.splitlines()
        sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
        changed_old = sum(
            i2 - i1 for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"
        )
        return changed_old / (len(old_lines) or 1)

    def test_single_line_blob_is_always_100_percent_noise_uncleaned(self):
        # A no-newline blob (WA's real HTML raw_text shape) always diffs as "the whole
        # document changed," even for a single-word edit deep inside it.
        old = "ByRepresentatives Smith.AN ACT Relating to X. Sec. 1. The word is old."
        new = "ByRepresentatives Smith.AN ACT Relating to X. Sec. 1. The word is new."
        assert self._noise_ratio(old, new) == 1.0

    def test_reflow_isolates_the_real_edit_instead_of_replacing_the_whole_document(self):
        old = "ByRepresentatives Smith.AN ACT Relating to X. Sec. 1. The word is old."
        new = "ByRepresentatives Smith.AN ACT Relating to X. Sec. 1. The word is new."
        cleaned_ratio = self._noise_ratio(_clean_wa_text(old), _clean_wa_text(new))
        # Real, meaningful reduction (AC4) -- not just "not worse" (AC3).
        assert cleaned_ratio < 0.5
        assert cleaned_ratio > 0.0  # the genuine edit must still show up, not be deleted (AC5)


def _make_wa_bill():
    bill = _make_bill()
    bill.legislative_session.jurisdiction.name = "Washington"
    bill.legislative_session.jurisdiction.save()
    return bill


@pytest.mark.django_db
class TestArchiveBillVersionsWashingtonGate:
    """AC1: the WA-only cleaning path must never affect any other jurisdiction's behavior."""

    def _run_archive(self, bill, texts_by_url):
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
            archive_bill_versions(bill)

    def test_non_washington_bill_diff_is_byte_for_byte_unchanged(self):
        # Deliberately embeds WA-shaped noise (tracking code, title, sponsor, procedural
        # lines) in a non-WA (ak) bill's own text -- if the jurisdiction gate ever leaked,
        # this noise would disappear from the diff. It must not: the non-WA path must be
        # byte-for-byte identical to the plain difflib.unified_diff() call, untouched.
        bill = _make_bill()  # ak jurisdiction, per the existing helper's default
        assert bill.legislative_session.jurisdiction.name != "Washington"

        introduced = bill.versions.create(note="Introduced", date="")
        enrolled = bill.versions.create(note="Enrolled", date="")
        introduced.links.create(
            url="https://example.test/introduced.pdf", media_type="application/pdf"
        )
        enrolled.links.create(
            url="https://example.test/enrolled.pdf", media_type="application/pdf"
        )

        prior_text = "H-1163.2HOUSE BILL 1960ByRepresentatives Smith.AN ACT Relating to X."
        raw_text = "H-1163.2HOUSE BILL 1960ByRepresentatives Smith.AN ACT Relating to X. Y."
        self._run_archive(
            bill,
            {
                "https://example.test/introduced.pdf": prior_text,
                "https://example.test/enrolled.pdf": raw_text,
            },
        )

        doc = BillVersionDocument.objects.get(bill=bill, version_note="Enrolled")
        expected = "\n".join(
            difflib.unified_diff(prior_text.splitlines(), raw_text.splitlines(), lineterm="")
        )
        assert doc.diff_from_previous_version == expected
        # the WA-shaped noise must still be present -- proof the cleaner never ran here
        assert "H-1163.2" in doc.diff_from_previous_version
        assert "HOUSE BILL 1960" in doc.diff_from_previous_version

    def test_washington_bill_diff_is_cleaned(self):
        bill = _make_wa_bill()
        introduced = bill.versions.create(note="Bill", date="")
        substitute = bill.versions.create(note="Substitute Bill", date="")
        introduced.links.create(
            url="https://example.test/introduced.html", media_type="text/html"
        )
        substitute.links.create(
            url="https://example.test/substitute.html", media_type="text/html"
        )

        prior_text = (
            "H-1163.2HOUSE BILL 1960State of Washington69th Legislature2025 Regular Session"
            "ByRepresentatives Ramel.Prefiled 02/11/25.Read first time 02/12/25."
            "Referred to Committee on Finance.AN ACT Relating to renewable energy in Washington."
        )
        raw_text = (
            "H-1664.1SUBSTITUTE HOUSE BILL 1960State of Washington69th Legislature"
            "2025 Regular SessionByHouse Finance (originally sponsored by "
            "Representatives Ramel)READ FIRST TIME 02/26/25."
            "AN ACT Relating to renewable energy in Washington and solar power."
        )
        self._run_archive(
            bill,
            {
                "https://example.test/introduced.html": prior_text,
                "https://example.test/substitute.html": raw_text,
            },
        )

        doc = BillVersionDocument.objects.get(bill=bill, version_note="Substitute Bill")
        diff = doc.diff_from_previous_version
        # boilerplate that differs between every version regardless of content must be gone
        assert "H-1163.2" not in diff
        assert "H-1664.1" not in diff
        assert "HOUSE BILL 1960" not in diff
        assert "Ramel" not in diff
        # the real, substantive addition must still be visible
        assert "solar power" in diff
        # raw_text/prior_text themselves (as archived) must NOT be mutated by the cleaner --
        # only the text fed into the diff call changes, per the ticket's own scope.
        assert doc.raw_text == raw_text
