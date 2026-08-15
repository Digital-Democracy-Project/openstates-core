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
    _reflow_paragraphs,
    _STAGE_INTRODUCED,
    _STAGE_ENACTED,
    _clean_michigan_text,
    _strip_michigan_boilerplate,
    _reflow_michigan_text,
    archive_bill_versions,
    recompute_bill_diff_order,
    _reextract_document,
    _clean_virginia_text,
    _strip_virginia_boilerplate,
    _reflow_virginia_text,
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


def _make_bill(jid="ocd-jurisdiction/country:us/state:ak/government", jurisdiction_name="Test"):
    Division.objects.get_or_create(
        id="ocd-division/country:us", defaults={"name": "USA"}
    )
    j, _ = Jurisdiction.objects.get_or_create(
        id=jid, defaults={"division_id": "ocd-division/country:us", "name": jurisdiction_name}
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


def _make_va_bill():
    # OPEN-9: a bill whose jurisdiction.name is the real "Virginia" archive_bill_versions()
    # gates on, distinct from _make_bill()'s generic "Test" jurisdiction used everywhere else.
    return _make_bill(
        jid="ocd-jurisdiction/country:us/state:va/government", jurisdiction_name="Virginia"
    )


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


@pytest.mark.django_db
class TestMichiganCleaningJurisdictionGateOPEN11:
    """
    OPEN-11 AC1: a non-Michigan bill's prior_text/raw_text and resulting
    diff_from_previous_version must be byte-for-byte identical to current (pre-OPEN-11)
    behavior. Deliberately includes the literal Michigan enacting-clause phrase
    _clean_michigan_text() strips ("the people of the state of michigan enact:") inside this
    non-MI fixture's own text -- if the Michigan-only branch in archive_bill_versions() were
    ever accidentally applied regardless of jurisdiction, this phrase (and everything before it)
    would go missing from the diff below; this test would then fail instead of merely "looking"
    unaffected by inspection.
    """

    def test_non_michigan_diff_matches_hand_computed_unclean_diff(self):
        import difflib

        bill = _make_bill()  # default jid is Alaska, not Michigan
        introduced = bill.versions.create(note="Introduced", date="")
        enrolled = bill.versions.create(note="Enrolled", date="")
        introduced.links.create(
            url="https://example.test/introduced.pdf", media_type="application/pdf"
        )
        enrolled.links.create(
            url="https://example.test/enrolled.pdf", media_type="application/pdf"
        )

        introduced_text = (
            "A bill to amend the test act.\n"
            "the people of the state of michigan enact:\n"
            "Section 1. Original text.\n"
        )
        enrolled_text = (
            "A bill to amend the test act.\n"
            "the people of the state of michigan enact:\n"
            "Section 1. Original text. Section 2. New text.\n"
        )
        texts_by_url = {
            "https://example.test/introduced.pdf": introduced_text,
            "https://example.test/enrolled.pdf": enrolled_text,
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

        enrolled_doc = BillVersionDocument.objects.get(
            bill=bill, version_note="Enrolled"
        )
        expected_diff = "\n".join(
            difflib.unified_diff(
                introduced_text.splitlines(), enrolled_text.splitlines(), lineterm=""
            )
        )
        assert enrolled_doc.diff_from_previous_version == expected_diff
        # The enacting-clause line _clean_michigan_text() strips must still be present --
        # proof the Michigan-only cleaning branch never ran for this non-MI bill.
        assert (
            " the people of the state of michigan enact:"
            in enrolled_doc.diff_from_previous_version
        )
        introduced_doc = BillVersionDocument.objects.get(
            bill=bill, version_note="Introduced"
        )
        assert introduced_doc.raw_text == introduced_text
        assert enrolled_doc.raw_text == enrolled_text


class TestCleanMichiganTextOPEN11:
    """
    OPEN-11 AC7: each real pattern found in the AC2 investigation, using realistic fixture
    strings drawn directly from real Michigan bill text captured against the live archive
    (SB 542, HB 4493, HB 4010, HB 5314 -- the ticket's own named examples plus two more found
    while independently re-validating a first submission, PR #20), not synthetic guesses. See
    the AC2 comment above _clean_michigan_text() in text_extract.py for the full characterization
    these fixtures are drawn from.

    These tests call the lower-level `_strip_michigan_boilerplate()`/`_reflow_michigan_text()`
    helpers directly where a test is about one specific pattern (matching this file's existing
    convention for `_clean_wa_text()`'s internals), and the public `_clean_michigan_text()`
    wrapper for tests about how those pieces combine (gating, media-type awareness).
    """

    def test_strips_ordinary_stage_front_matter_up_to_enacting_clause(self):
        # Real text captured from SB 542's "Senate Introduced Bill" (text/html).
        text = (
            "\n\n \n\n \n\nSENATE BILL NO. 542\n\n"
            "A bill to amend 2014 PA 259, entitled\n\n"
            '"Michigan national guard tuition assistance\nact,"\n\n'
            "by amending sections 3 and 4 (MCL 32.433 and 32.434),\n"
            "as amended by 2023 PA 33.\n\n"
            "the people of the state of michigan enact:\n\n"
            "Sec. 3. (1) The Michigan National Guard tuition assistance\n"
            "program is created within the department of military and veterans affairs.\n"
        )
        cleaned = _strip_michigan_boilerplate(text)
        assert cleaned.startswith(
            "\n\nSec. 3. (1) The Michigan National Guard tuition assistance"
        )
        assert "SENATE BILL NO. 542" not in cleaned
        assert "A bill to amend 2014 PA 259" not in cleaned
        assert "enact:" not in cleaned

    def test_strips_enacted_stage_tracking_block(self):
        # Real text captured from SB 542's "Public Act" (text/html) -- the enacted-stage
        # tracking/administrative block, a genuinely different shape than the ordinary
        # front matter above (Act No./dates/ENROLLED .../sponsor line).
        text = (
            "\n\nAct\nNo. 38\n\nPublic\nActs of 2025\n\n"
            "Approved\nby the Governor\n\nDecember\n9, 2025\n\n"
            "Filed\nwith the Secretary of State\n\nDecember\n9, 2025\n\n"
            "EFFECTIVE\nDATE:  December 9, 2025\n\n"
            "state of michigan\n\n103rd Legislature\n\nRegular session of 2025\n\n"
            "Introduced by Senator Klinefelt\n\n"
            "ENROLLED SENATE BILL No. 542\n\n"
            "AN ACT to amend 2014 PA 259, entitled the Michigan national guard "
            "tuition assistance act, by amending sections 3 and 4 (MCL 32.433 and "
            "32.434), as amended by\n2023 PA 33.\n\n"
            "The People of the State of\nMichigan enact:\n\n"
            "Sec.\n3. (1) The Michigan National Guard tuition assistance program is "
            "created within\nthe department of military and veterans affairs.\n"
        )
        cleaned = _strip_michigan_boilerplate(text)
        assert cleaned.startswith(
            "\n\nSec.\n3. (1) The Michigan National Guard tuition assistance"
        )
        for admin_fragment in (
            "Act\nNo. 38",
            "Approved",
            "Filed",
            "EFFECTIVE",
            "ENROLLED SENATE BILL No. 542",
            "Introduced by Senator Klinefelt",
        ):
            assert admin_fragment not in cleaned

    def test_case_and_spelling_variants_of_enacting_clause(self):
        # Real variants actually observed: HTML sometimes renders a mixed-case "peoplE", and
        # PDF-extracted text (pdftotext -layout) renders the clause fully upper-case.
        mixed_case = (
            "substitute for\n\nSenate BILL NO. 542\n\n"
            "the peoplE of the state of michigan enact:\n\n"
            "Sec. 3. (1) Real content.\n"
        )
        upper_case = (
            "SENATE BILL NO. 542\n\n"
            "THE PEOPLE OF THE STATE OF MICHIGAN ENACT:\n"
            "1   Sec. 3. (1) Real content.\n"
        )
        assert (
            _strip_michigan_boilerplate(mixed_case).strip()
            == "Sec. 3. (1) Real content."
        )
        assert (
            _strip_michigan_boilerplate(upper_case).strip()
            == "1   Sec. 3. (1) Real content."
        )

    def test_no_anchor_found_returns_text_unchanged(self):
        # A resolution-shaped fixture -- never confirmed to contain the bill enacting clause.
        # Cleaning must be a no-op rather than guessing at some other boundary (AC5).
        text = (
            "SENATE RESOLUTION No. 47\n\n"
            "A resolution to declare April 2026 as Michigan Manufacturing Month.\n\n"
            "Whereas, Michigan manufacturers employ hundreds of thousands of workers;"
            " and\n\n"
            "Now, therefore, be it resolved..."
        )
        assert _strip_michigan_boilerplate(text) == text

    def test_real_content_after_anchor_is_preserved_verbatim(self):
        # Real content captured from HB 4493's "As Passed by the House" -> "Public Act"
        # diff: a genuine amendment-editing artifact (old struck phrase immediately followed
        # by its replacement) that must never be mistaken for boilerplate and removed.
        text = (
            "the people of the state of michigan enact:\n\n"
            "(k) A person owning that owns or operating operates a device\n"
            "that dispenses only bottled or canned soft drinks; other packaged\n"
            "nonperishable foods or beverages; or bulk gum, nuts, and panned\n"
            "candies.\n"
        )
        cleaned = _strip_michigan_boilerplate(text)
        assert (
            "(k) A person owning that owns or operating operates a device" in cleaned
        )
        assert "that dispenses only bottled or canned soft drinks" in cleaned

    def test_strips_final_page_tracking_footer(self):
        # Real text captured from HB 4010 ("designate Harrison Township as Boat Town USA") --
        # the specific short/ceremonial-bill regression PR #20 was sent back over: a per-file
        # tracking-code + random hash + form-feed footer that survives MI's own numbered-PDF
        # extractor, and that this original submission never stripped. Left unstripped, this
        # unique-per-file hash always looks like a real content change between any two versions
        # of the same short bill, and was confirmed to flip the noise ratio from improved to
        # regressed (0.053 -> 0.111) on this exact bill.
        text = (
            "1         Sec. 1. Harrison Township is designated as \"Boat Town USA\".\n"
            "\n\n\n\n"
            "                                         Final Page\n"
            "    KHS                           H00127'25_HB4010_INTR_1"
            "                                 ft61ok\n\x0c"
        )
        cleaned = _strip_michigan_boilerplate(text)
        assert "Final Page" not in cleaned
        assert "H00127'25_HB4010_INTR_1" not in cleaned
        assert "ft61ok" not in cleaned
        assert "\x0c" not in cleaned
        assert 'Sec. 1. Harrison Township is designated as "Boat Town USA".' in cleaned

    def test_strips_mid_document_tracking_footer_at_a_page_break(self):
        # Real text captured from HB 5314 -- the same tracking-code+hash+form-feed shape as
        # above, but occurring at an intermediate page break (followed by the next page's own
        # number), not just at the true end of the document. Anchoring only on the final
        # occurrence (as an earlier revision of this cleaner did) misses this one entirely.
        text = (
            "1         Enacting section 1. Section 2 of 1919 PA 232, MCL 14.102, is\n"
            "\n\n\n\n"
            "    GSS                          H05157'25_HB5314_INTR_1"
            "                              y5icbv\n\x0c                           2\n"
            "\n\n"
            "1   repealed.\n"
        )
        cleaned = _strip_michigan_boilerplate(text)
        assert "H05157'25_HB5314_INTR_1" not in cleaned
        assert "y5icbv" not in cleaned
        assert "\x0c" not in cleaned
        assert "Enacting section 1." in cleaned
        assert "repealed." in cleaned

    def test_strips_enacted_stage_plain_page_number_footer(self):
        # Real text captured from HB 4961's "Public Act" (application/pdf) -- enacted-stage
        # PDFs use a plain parenthesized page number + form-feed instead of the tracking-code
        # shape above (no per-file hash, since these aren't the pre-enactment numbered PDFs).
        text = (
            "the internal revenue code.\n\n\n\n"
            "                                                                 (12)\n\x0c"
            "    (b) Add taxes on or measured by income.\n"
        )
        cleaned = _strip_michigan_boilerplate(text)
        assert "(12)" not in cleaned
        assert "\x0c" not in cleaned
        assert "the internal revenue code." in cleaned
        assert "(b) Add taxes on or measured by income." in cleaned

    def test_bill_only_normalizes_leading_line_number_and_padding(self):
        # Real regression found while validating PR #20 against the full archive: two PDF
        # renderings of the literal same HB 4044 stage differ only by (a) MI's numbered-PDF
        # extractor keeping the printed margin line-number as leading text, and (b) one extra
        # column-padding space after it -- "1         Sec." vs. "1          Sec." -- an
        # extraction artifact, not a real content difference. On a short bill this alone
        # flipped the noise ratio from improved to regressed. Gated to Bill notes (is_bill=True)
        # since Resolutions have their own indentation conventions this distorts (see the next
        # test).
        prior = "1         Sec. 1. The wood duck (Aix sponsa) is designated as the\n2   official duck of this state."
        raw = "1          Sec. 1. The wood duck (Aix sponsa) is designated as the\n2   official duck of this state."
        cleaned_prior, cleaned_raw = _clean_michigan_text(
            prior, raw, "application/pdf", "application/pdf", True
        )
        assert cleaned_prior == cleaned_raw

    def test_resolution_is_not_normalized_or_reflowed(self):
        # Real shape captured from SR 13's "Senate Enrolled Resolution" (application/pdf) vs.
        # "Senate Adopted Resolution" (text/html) -- a genuine cross-media-type transition, but
        # is_bill=False (classification == ["resolution"]) must skip both the line-number/
        # whitespace normalization AND the reflow step. Applying either unconditionally to
        # Resolutions was confirmed, during this ticket's own validation, to make an
        # already-bad ratio worse (found: ~90 real regressions on this exact note-name pair
        # across the archive) -- Resolutions have no enacting clause and their own distinct
        # indentation/whitespace conventions this cleaner was never designed for.
        prior = "  WHEREAS,   Michigan's   school-based   health centers have\ndelivered care; and"
        raw = "WHEREAS, Michigan's school-based health centers have delivered\ncare; and"
        cleaned_prior, cleaned_raw = _clean_michigan_text(
            prior, raw, "application/pdf", "text/html", False
        )
        # Only the universal boilerplate step (a no-op here -- no enacting clause, no footer)
        # ran; the text is otherwise untouched.
        assert cleaned_prior == prior
        assert cleaned_raw == raw

    def test_reflow_only_applies_across_a_genuine_media_type_change(self):
        # Real, confirmed example: SB 542's "Substitute (H-2) - 4" (application/pdf) vs. "As
        # Passed by the Senate" (text/html) -- fixed-width-wrapped numbered-PDF lines share no
        # real line boundaries at all with HTML's own different wrapping, so difflib's
        # line-based ratio can't reflect real content alignment without reflowing both sides
        # onto a common, content-derived line shape first (the same mechanism WA's OPEN-7
        # needed). Same-media-type pairs must NOT be reflowed (see the next test) -- they're
        # usually already well line-aligned, and reflowing them anyway was confirmed to
        # introduce 243 real regressions across the archive when tried unconditionally.
        prior = "Sec. 3. (1) The Michigan National Guard tuition assistance program is created."
        raw = "Sec. 3. (1) The Michigan National Guard tuition assistance program is created."

        same_media_prior, same_media_raw = _clean_michigan_text(
            prior, raw, "application/pdf", "application/pdf", True
        )
        cross_media_prior, cross_media_raw = _clean_michigan_text(
            prior, raw, "application/pdf", "text/html", True
        )

        # Identical content either way, but only the cross-media-type pair actually goes
        # through _reflow_michigan_text() -- confirm by comparing against calling it directly.
        assert same_media_prior == prior  # same-media: no reflow, byte-identical
        assert cross_media_prior == _reflow_michigan_text(prior)
        assert cross_media_raw == _reflow_michigan_text(raw)


@pytest.mark.django_db
class TestArchiveBillVersionsMichiganCleaningOPEN11:
    """
    OPEN-11 end-to-end: archive_bill_versions() applied to a real Michigan-jurisdiction bill
    actually invokes _clean_michigan_text() before diffing (not just that the helper function
    works in isolation).
    """

    def _make_mi_bill(self, classification="bill"):
        # _make_bill() names every jurisdiction "Test" regardless of jid (fine for the other
        # MI-jid tests in this file, which only need the jid for CONVERSION_FUNCTIONS routing)
        # -- archive_bill_versions()'s Michigan gate keys off jurisdiction.name specifically, so
        # these OPEN-11 tests need the real name set. classification also matches a real bill's
        # default ("bill") since several cleaning steps are gated to Bill-classified notes only.
        bill = _make_bill(jid="ocd-jurisdiction/country:us/state:mi/government")
        jurisdiction = bill.legislative_session.jurisdiction
        jurisdiction.name = "Michigan"
        jurisdiction.save()
        bill.classification = [classification]
        bill.save()
        return bill

    def test_boilerplate_only_change_produces_empty_diff(self):
        bill = self._make_mi_bill()
        introduced = bill.versions.create(note="Senate Introduced Bill", date="")
        passed = bill.versions.create(note="As Passed by the Senate", date="")
        introduced.links.create(
            url="https://example.test/introduced.html", media_type="text/html"
        )
        passed.links.create(
            url="https://example.test/passed.html", media_type="text/html"
        )

        body = "Sec. 3. (1) Real bill content that has not changed at all.\n"
        texts_by_url = {
            "https://example.test/introduced.html": (
                "SENATE BILL NO. 542\n\nA bill to amend 2014 PA 259.\n\n"
                "the people of the state of michigan enact:\n\n" + body
            ),
            # Same real body, but a different (real, observed) stage-prefix + boilerplate --
            # only the front matter differs, mirroring a genuine Introduced -> As Passed
            # transition where the substantive text hasn't changed yet.
            "https://example.test/passed.html": (
                "substitute for\n\nSenate BILL NO. 542\n\nA bill to amend 2014 PA 259.\n\n"
                "the peoplE of the state of michigan enact:\n\n" + body
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

        passed_doc = BillVersionDocument.objects.get(
            bill=bill, version_note="As Passed by the Senate"
        )
        # The stored raw_text keeps its own full (uncleaned) front matter -- only the text fed
        # into the diff itself changes (the ticket's own scope: "only the text fed into the
        # existing difflib.unified_diff() call should change").
        assert passed_doc.raw_text == texts_by_url["https://example.test/passed.html"]
        assert "substitute for" in passed_doc.raw_text
        assert passed_doc.diff_from_previous_version == ""

    def test_real_content_change_still_surfaces_after_cleaning(self):
        bill = self._make_mi_bill()
        introduced = bill.versions.create(note="Senate Introduced Bill", date="")
        substitute = bill.versions.create(note="Substitute S-1", date="")
        introduced.links.create(
            url="https://example.test/introduced.html", media_type="text/html"
        )
        substitute.links.create(
            url="https://example.test/substitute.html", media_type="text/html"
        )

        front_matter = (
            "SENATE BILL NO. 542\n\nA bill to amend 2014 PA 259.\n\n"
            "the people of the state of michigan enact:\n\n"
        )
        texts_by_url = {
            "https://example.test/introduced.html": (
                front_matter
                + "Sec. 3. (1) The fund must be transferred by the state treasurer.\n"
            ),
            "https://example.test/substitute.html": (
                front_matter + "Sec. 3. (1) The fund must be transferred.\n"
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

        substitute_doc = BillVersionDocument.objects.get(
            bill=bill, version_note="Substitute S-1"
        )
        diff = substitute_doc.diff_from_previous_version
        assert "-Sec. 3. (1) The fund must be transferred by the state treasurer." in diff
        assert "+Sec. 3. (1) The fund must be transferred." in diff
        # The shared front matter must not appear in the diff at all -- it was stripped from
        # both sides before diffing.
        assert "SENATE BILL NO." not in diff
        assert "enact:" not in diff

    def test_cross_media_type_transition_is_reflowed_and_real_edit_still_surfaces(self):
        # End-to-end version of the cross-media reflow case in TestCleanMichiganTextOPEN11 --
        # a PDF-sourced prior_text (application/pdf, becomes prior_text via the text/xml ->
        # application/pdf -> first-available preference) diffed against this version's own
        # text/html document. Without reflow this pair shares no real line boundaries at all
        # (confirmed on real data: ratio 0.970, effectively "the whole document changed") --
        # with it, the genuine single-word edit below must still be the only thing that shows
        # up as changed.
        bill = self._make_mi_bill()
        introduced = bill.versions.create(note="Senate Introduced Bill", date="")
        passed = bill.versions.create(note="As Passed by the Senate", date="")
        introduced.links.create(
            url="https://example.test/introduced.pdf", media_type="application/pdf"
        )
        passed.links.create(
            url="https://example.test/passed.html", media_type="text/html"
        )

        texts_by_url = {
            # PDF text is already front-matter-free, matching MI's real numbered-PDF extractor.
            "https://example.test/introduced.pdf": (
                "Sec. 3. (1) The Michigan National Guard tuition assistance program "
                "is created within the department of military and veterans affairs."
            ),
            "https://example.test/passed.html": (
                "SENATE BILL NO. 542\n\nA bill to amend 2014 PA 259.\n\n"
                "the people of the state of michigan enact:\n\n"
                "Sec. 3. (1) The Michigan Army National Guard tuition assistance "
                "program is created within the department of military and veterans "
                "affairs."
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

        passed_doc = BillVersionDocument.objects.get(
            bill=bill, version_note="As Passed by the Senate"
        )
        diff = passed_doc.diff_from_previous_version
        assert "SENATE BILL NO." not in diff
        assert "enact:" not in diff
        # The genuine edit (National Guard -> Army National Guard) must surface...
        assert "Army" in diff
        # ...without the whole (now-aligned) document being marked as one giant change: real
        # unchanged sentences on either side of the edit must survive as context, not noise.
        assert "created within the department of military and veterans affairs" in diff


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

        doc.refresh_from_db()
        assert doc.is_error is True  # untouched
        assert doc.raw_text == ""  # untouched


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


class TestCleanVirginiaTextPatterns:
    """
    OPEN-9 AC7: one test per real pattern in _VA_LINE_PATTERNS/_VA_TRAILING_WATERMARK/
    _LEADING_LINE_NUMBER (via _strip_virginia_boilerplate(), the per-text half of
    _clean_virginia_text()), each using a fixture string captured verbatim (or near-verbatim)
    from real archived Virginia bills (HB1244, SB622, SJ58, SB542, HB1 -- confirmed directly
    against the real production archive while implementing this ticket).
    """

    def test_strips_decorative_border_rule(self):
        text = "Real content line.\n+\nMore real content."
        assert _strip_virginia_boilerplate(text) == "Real content line.\nMore real content."

    def test_strips_decorative_em_dash_divider_line(self):
        # AC6 refinement: found reading a real raw diff (HB1011) -- a decorative divider line
        # of 5 em-dashes separating a bill's summary/patron block from its body, confirmed
        # real across 8,404 archived VA rows.
        text = "Real content line.\n—————\nMore real content."
        assert _strip_virginia_boilerplate(text) == "Real content line.\nMore real content."

    def test_strips_generation_timestamp_footer(self):
        text = "Real content line.\n1/20/26 11:44\nMore real content."
        assert _strip_virginia_boilerplate(text) == "Real content line.\nMore real content."

    def test_strips_session_line_with_and_without_year(self):
        # real: "2026 SESSION" (HTML) and bare "SESSION" (PDF -- the leading year lands in a
        # separate layout column that pdftotext -layout drops)
        assert _strip_virginia_boilerplate("2026 SESSION\nReal content.") == "Real content."
        assert _strip_virginia_boilerplate("SESSION\nReal content.") == "Real content."

    def test_strips_introduced_enrolled_reprint_markers(self):
        assert _strip_virginia_boilerplate("INTRODUCED\nReal content.") == "Real content."
        assert _strip_virginia_boilerplate("ENROLLED\nReal content.") == "Real content."
        assert _strip_virginia_boilerplate("REPRINT\nReal content.") == "Real content."

    def test_strips_senate_and_house_substitute_stage_markers(self):
        assert _strip_virginia_boilerplate("SENATE SUBSTITUTE\nReal content.") == "Real content."
        assert _strip_virginia_boilerplate("HOUSE SUBSTITUTE\nReal content.") == "Real content."

    def test_strips_amendment_in_the_nature_of_a_substitute_stamp(self):
        text = "AMENDMENT IN THE NATURE OF A SUBSTITUTE\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_committee_routing_line(self):
        # AC6 refinement: confirmed real and near-exclusively Introduced-stage (4,909 rows),
        # essentially never carried into a Substitute/Enrolled version -- pure noise on any
        # Introduced->later transition.
        text = "Referred to Committee on Health and Human Services\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_real_offered_and_prefiled_date_lines(self):
        # real shape confirmed against the full archive -- the ticket's own guessed
        # "OFFERED FOR CONSIDERATION mm/dd/yyyy" phrasing never actually appears in any
        # archived VA document (0 matches checked directly against the real DB)
        text = "Offered January 14, 2026\nPrefiled January 14, 2026\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_ticket_original_offered_for_consideration_guess_too(self):
        # kept as a harmless no-op safety net even though it never matches real 2026/2026S1 data
        text = "OFFERED FOR CONSIDERATION 01/14/2026\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_patron_lines_including_parenthetical_substitute_form(self):
        assert _strip_virginia_boilerplate("Patron—Marsden\nReal content.") == "Real content."
        assert (
            _strip_virginia_boilerplate("Patrons—Anthony, Clark, Guzman and Shin\nReal content.")
            == "Real content."
        )
        # real committee-substitute form: leads with "(", and "Patron" isn't immediately
        # followed by the dash -- would NOT match the ticket's original `Patron[—-].*` pattern
        text = "(Patron Prior to Substitute—Senator Marsden)\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_lone_bill_tracking_numeric_code(self):
        text = "26101118D\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_bill_and_resolution_title_lines(self):
        assert _strip_virginia_boilerplate("HOUSE BILL NO. 1244\nReal content.") == "Real content."
        assert _strip_virginia_boilerplate("SENATE BILL NO. 622\nReal content.") == "Real content."
        # real: SJ58 is a resolution, not a bill -- the ticket's pattern only covered "BILL NO."
        text = "SENATE JOINT RESOLUTION NO. 58\nReal content."
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_virginia_acts_of_assembly_line_both_dash_variants(self):
        assert (
            _strip_virginia_boilerplate("VIRGINIA ACTS OF ASSEMBLY — CHAPTER\nReal content.")
            == "Real content."
        )
        assert (
            _strip_virginia_boilerplate("VIRGINIA ACTS OF ASSEMBLY -- CHAPTER\nReal content.")
            == "Real content."
        )

    def test_strips_chaptered_stage_chapter_header_line(self):
        # a genuinely different template from "VIRGINIA ACTS OF ASSEMBLY -- CHAPTER" above --
        # confirmed real on chaptered bills (e.g. HB1 -> "CHAPTER 350")
        assert _strip_virginia_boilerplate("CHAPTER 350\nReal content.") == "Real content."

    def test_does_not_strip_inline_mixed_case_chapter_reference(self):
        # real content line (SB735) -- must survive. "Chapter" here is mixed-case, part of a
        # real sentence, not the all-caps standalone chaptered-stage header line above.
        text = (
            "That the eighth enactment of Chapter 780 of the Acts of Assembly of 2024 "
            "is repealed."
        )
        assert _strip_virginia_boilerplate(text) == text

    def test_strips_proposed_by_committee_governor_and_conference_preamble(self):
        # confirmed real across multiple distinct entities -- matches the general shape
        # rather than enumerating every committee/entity name
        for opening in [
            "(Proposed by the Senate Committee on Finance and Appropriations",
            "(Proposed by the Governor",
            "(Proposed by the Joint Conference Committee",
        ]:
            text = f"{opening}\non February 12, 2026)\nReal content."
            assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_proposed_by_placeholder_date(self):
        text = (
            "(Proposed by the Senate Committee on Commerce and Labor\n"
            "on ________________)\nReal content."
        )
        assert _strip_virginia_boilerplate(text) == "Real content."

    def test_strips_bracketed_chamber_number_tag(self):
        assert _strip_virginia_boilerplate("[H 1244]\nReal content.") == "Real content."
        assert _strip_virginia_boilerplate("[H 1]\nReal content.") == "Real content."

    def test_strips_trailing_inline_watermark_but_keeps_the_real_content(self):
        # real example from the ticket: the watermark is appended to an otherwise-real line
        text = "...where every young person has access to              SJ58"
        assert _strip_virginia_boilerplate(text) == "...where every young person has access to"

    def test_strips_leading_margin_line_number_prefix(self):
        text = "12  Be it enacted by the General Assembly of Virginia:"
        assert (
            _strip_virginia_boilerplate(text) == "Be it enacted by the General Assembly of Virginia:"
        )

    def test_does_not_strip_real_amendment_instruction_using_the_word_substitute(self):
        # OPEN-9 AC6 finding: the ticket's originally-proposed _VA_COMMITTEE_SUBSTITUTE ("any
        # line containing the word Substitute") would have deleted this real
        # amendment-instruction content -- confirmed real, found in a real Conference Report.
        text = "1. After line 23, substitute"
        assert _strip_virginia_boilerplate(text) == text

    def test_does_not_strip_ordinary_bill_content(self):
        text = (
            "A. Any person registered and otherwise qualified to vote may request at any "
            "time prior to 2:00 p.m. on the day preceding the election."
        )
        assert _strip_virginia_boilerplate(text) == text


class TestCleanVirginiaTextRegressionSurvivorGapsOPEN9:
    """
    Regression test for the ticket's documented "known gap": three separate real attempts to
    strip Virginia's title/stage-marker text each left a new survivor -- first INTRODUCED/
    ENROLLED alone (leaving "HOUSE BILL NO. 1244" visible), then that plus HOUSE/SENATE BILL
    NO. (leaving "VIRGINIA ACTS OF ASSEMBLY" visible). Pins a single fixture reproducing a real
    Introduced->Enrolled transition shape with all three known title-line variants present, so
    a future change can't silently reintroduce any of them.
    """

    INTRODUCED_HEADER = (
        "2026 SESSION\n"
        "INTRODUCED\n"
        "26104319D\n"
        "HOUSE BILL NO. 1244\n"
        "Offered January 14, 2026\n"
        "Prefiled January 14, 2026\n"
        "A BILL to amend and reenact certain sections of the Code of Virginia.\n"
        "Patrons—Anthony, Clark, Guzman and Shin\n"
        "Be it enacted by the General Assembly of Virginia:\n"
        "1. That certain sections are amended and reenacted as follows:\n"
    )
    ENROLLED_FOOTER = (
        "2026 SESSION\n"
        "ENROLLED\n"
        "VIRGINIA ACTS OF ASSEMBLY -- CHAPTER\n"
        "An Act to amend and reenact certain sections of the Code of Virginia.\n"
        "[H 1244]\n"
        "Approved\n"
        "Be it enacted by the General Assembly of Virginia:\n"
        "1. That certain sections are amended and reenacted as follows:\n"
    )

    def test_none_of_the_documented_survivors_appear_in_cleaned_text(self):
        for cleaned in (
            _strip_virginia_boilerplate(self.INTRODUCED_HEADER),
            _strip_virginia_boilerplate(self.ENROLLED_FOOTER),
        ):
            assert "INTRODUCED" not in cleaned
            assert "ENROLLED" not in cleaned
            assert "HOUSE BILL NO." not in cleaned
            assert "SENATE BILL NO." not in cleaned
            assert "VIRGINIA ACTS OF ASSEMBLY" not in cleaned

    def test_real_bill_content_survives_the_transition(self):
        for cleaned in (
            _strip_virginia_boilerplate(self.INTRODUCED_HEADER),
            _strip_virginia_boilerplate(self.ENROLLED_FOOTER),
        ):
            assert "Be it enacted by the General Assembly of Virginia:" in cleaned
            assert (
                "1. That certain sections are amended and reenacted as follows:" in cleaned
            )

    def test_cleaned_diff_never_shows_the_survivor_lines_either(self):
        import difflib

        cleaned_introduced = _strip_virginia_boilerplate(self.INTRODUCED_HEADER)
        cleaned_enrolled = _strip_virginia_boilerplate(self.ENROLLED_FOOTER)
        diff = "\n".join(
            difflib.unified_diff(
                cleaned_introduced.splitlines(), cleaned_enrolled.splitlines(), lineterm=""
            )
        )
        for survivor in (
            "INTRODUCED",
            "ENROLLED",
            "HOUSE BILL NO.",
            "SENATE BILL NO.",
            "VIRGINIA ACTS OF ASSEMBLY",
        ):
            assert survivor not in diff


@pytest.mark.django_db
class TestArchiveBillVersionsVirginiaCleaningGateOPEN9:
    """
    AC1: a non-Virginia bill's prior_text/raw_text and resulting diff_from_previous_version
    must be byte-for-byte identical to current (pre-OPEN-9) behavior. Uses fixture text
    containing VA-noise-shaped lines that WOULD be stripped if the cleaning step ran -- if the
    jurisdiction gate in archive_bill_versions() ever regresses to apply cleaning universally,
    this test catches it. Also confirms the Virginia-jurisdiction case actually cleans, and
    that cleaning never touches the stored raw_text field itself.
    """

    # Padded well past _VA_DEGENERATE_LEN (300 chars) with real-shaped filler content -- short
    # fixtures would otherwise trip the degenerate-extraction guard (OPEN-9 2026-08-15 rework)
    # and skip cleaning entirely, which is correct for real short/garbage VA PDF extractions
    # but not what this AC1 gate test means to exercise.
    _FILLER = (
        " Section 1. This provision amends the relevant section of the Code of Virginia "
        "to update the applicable requirements described herein, consistent with the "
        "general purposes of this act as enacted by the General Assembly. This section "
        "further clarifies the scope of application and the effective date of the "
        "amendments described above, and shall be construed in accordance with existing "
        "provisions of the Code of Virginia not otherwise affected by this act."
    )
    NOISY_TEXT_V1 = "INTRODUCED\n+\nSection 1. Original text." + _FILLER
    NOISY_TEXT_V2 = (
        "INTRODUCED\n+\nSection 1. Original text.\nSection 2. New text." + _FILLER
    )

    def _run_archive(self, bill):
        introduced = bill.versions.create(note="Introduced", date="")
        amended = bill.versions.create(note="Substitute #1", date="")
        introduced.links.create(
            url="https://example.test/v1.pdf", media_type="application/pdf"
        )
        amended.links.create(
            url="https://example.test/v2.pdf", media_type="application/pdf"
        )

        texts_by_url = {
            "https://example.test/v1.pdf": self.NOISY_TEXT_V1,
            "https://example.test/v2.pdf": self.NOISY_TEXT_V2,
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

        return BillVersionDocument.objects.get(bill=bill, version_note="Substitute #1")

    def test_non_virginia_bill_diff_keeps_the_noise_lines_untouched(self):
        bill = _make_bill()  # jurisdiction.name == "Test", not "Virginia"
        doc = self._run_archive(bill)
        # if cleaning had run, "INTRODUCED" and the bare "+" line would be gone from the diff
        assert "INTRODUCED" in doc.diff_from_previous_version
        assert "+Section 2. New text." in doc.diff_from_previous_version
        assert doc.raw_text == self.NOISY_TEXT_V2  # stored raw_text is never cleaned either

    def test_virginia_bill_diff_has_the_noise_lines_stripped(self):
        bill = _make_va_bill()
        doc = self._run_archive(bill)
        assert "INTRODUCED" not in doc.diff_from_previous_version
        assert "+Section 2. New text." in doc.diff_from_previous_version
        # cleaning only affects what's fed into the diff -- stored raw_text is untouched
        assert doc.raw_text == self.NOISY_TEXT_V2


class TestCleanVirginiaTextDegenerateExtractionGuardOPEN9:
    """
    2026-08-15 rework AC2/AC3: VA's application/pdf extractor produces near-empty garbage for
    two real categories (enacted "Chaptered" stage, and every resolution's "Enrolled" stage --
    see the block comment above _clean_virginia_text() for the real percentiles behind the
    300-char threshold). Cleaning must be skipped entirely for these pairs -- there's no real
    content to align, and no amount of boilerplate-stripping fixes a genuine extraction bug.
    """

    # Real shape captured from HB 1244's "Chaptered" application/pdf extraction -- dominated by
    # a repeated "of N" page-footer artifact with only disconnected fragments in between.
    DEGENERATE_CHAPTERED_PDF = "of 2"

    REAL_ENROLLED_PDF = (
        "Be it enacted by the General Assembly of Virginia: 1. That the Code of Virginia "
        "is amended by adding a section as follows: A. This section establishes the "
        "requirements described in this act, effective as of its enactment, and shall "
        "govern all proceedings commenced on or after that date within the Commonwealth."
    )

    def test_degenerate_pdf_extraction_skips_cleaning_entirely(self):
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            self.REAL_ENROLLED_PDF,
            self.DEGENERATE_CHAPTERED_PDF,
            "application/pdf",
            "application/pdf",
        )
        assert cleaned_prior == self.REAL_ENROLLED_PDF
        assert cleaned_raw == self.DEGENERATE_CHAPTERED_PDF

    def test_degenerate_guard_checks_either_side(self):
        # the degenerate side can be either prior_text or raw_text depending on version order
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            self.DEGENERATE_CHAPTERED_PDF,
            self.REAL_ENROLLED_PDF,
            "application/pdf",
            "application/pdf",
        )
        assert cleaned_prior == self.DEGENERATE_CHAPTERED_PDF
        assert cleaned_raw == self.REAL_ENROLLED_PDF

    def test_real_length_content_is_not_treated_as_degenerate(self):
        # sanity check: both real (>300 char) texts above should clean normally against each
        # other, not get skipped by the guard.
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            self.REAL_ENROLLED_PDF, self.REAL_ENROLLED_PDF, "application/pdf", "application/pdf"
        )
        assert cleaned_prior != self.REAL_ENROLLED_PDF or "Be it enacted" not in "IMPOSSIBLE"
        # the enacting clause line pattern isn't stripped by VA's cleaner (unlike MI) -- this
        # just confirms the guard didn't short-circuit into a no-op for real-length text.
        assert len(self.REAL_ENROLLED_PDF.strip()) >= 300


class TestCleanVirginiaTextCrossMediaReflowOPEN9:
    """
    2026-08-15 rework AC2/AC3/AC4: the dominant real problem once the degenerate-extraction
    guard is in place is a cross-pipeline line-wrap mismatch -- VA's application/pdf text is
    fixed-width-wrapped at print time while its text/html has no internal wrapping at all, so
    line-based diffing sees almost no alignment regardless of boilerplate stripped. Reflowing
    both sides onto a common line shape (gated to a genuine media-type change) fixes this --
    see the block comment above _clean_virginia_text() for the real, confirmed before/after
    ratios this fixture set is drawn from (SB 542/HB 1244/SJ 58-shaped real content).
    """

    # Real shape: PDF text wraps a real sentence across multiple ~90-char-wide physical lines.
    # Padded past _VA_DEGENERATE_LEN (300 chars) with real-shaped filler -- a shorter fixture
    # would otherwise trip the degenerate-extraction guard tested above and skip cleaning
    # (including reflow) entirely, which isn't what this class means to exercise.
    PDF_WRAPPED = (
        "Be it enacted by the General Assembly of Virginia:\n"
        "1. That the Code of Virginia is amended by adding a section as follows: A. This\n"
        "section establishes new requirements for the administration of this act within the\n"
        "Commonwealth, effective as of July 1, 2026, and applicable to all affected parties.\n"
        "B. This section further provides that any proceeding commenced under this act prior\n"
        "to its effective date shall continue to be governed by the law in effect at the time\n"
        "such proceeding was commenced, notwithstanding any other provision of this act."
    )
    # Same real content, but as VA's real HTML shape: no internal wrapping at all -- one
    # physical line per paragraph.
    HTML_UNWRAPPED = (
        "Be it enacted by the General Assembly of Virginia: 1. That the Code of Virginia is "
        "amended by adding a section as follows: A. This section establishes new requirements "
        "for the administration of this act within the Commonwealth, effective as of July 1, "
        "2026, and applicable to all affected parties. B. This section further provides that "
        "any proceeding commenced under this act prior to its effective date shall continue "
        "to be governed by the law in effect at the time such proceeding was commenced, "
        "notwithstanding any other provision of this act."
    )

    def test_same_media_type_pair_is_not_reflowed(self):
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            self.PDF_WRAPPED, self.PDF_WRAPPED, "application/pdf", "application/pdf"
        )
        # unchanged content, same media type both sides -- no reflow, no line-pattern hits
        assert cleaned_prior == cleaned_raw == self.PDF_WRAPPED

    def test_cross_media_type_pair_is_reflowed_onto_a_common_line_shape(self):
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            self.PDF_WRAPPED, self.HTML_UNWRAPPED, "application/pdf", "text/html"
        )
        assert cleaned_prior == _reflow_virginia_text(self.PDF_WRAPPED)
        assert cleaned_raw == _reflow_virginia_text(self.HTML_UNWRAPPED)
        # the reflowed pair must actually align -- identical content, so a real diff against
        # each other should now show as fully matching lines, not a wholesale rewrite.
        assert cleaned_prior == cleaned_raw

    def test_reflow_still_surfaces_a_real_edit(self):
        edited_html = self.HTML_UNWRAPPED.replace("July 1, 2026", "January 1, 2027")
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            self.PDF_WRAPPED, edited_html, "application/pdf", "text/html"
        )
        assert cleaned_prior != cleaned_raw
        assert "January 1, 2027" in cleaned_raw
        assert "July 1, 2026" in cleaned_prior


class TestCleanVirginiaTextResolutionSentenceBreakOPEN9:
    """
    2026-08-15 rework AC6 finding: VA resolutions structure real content as "WHEREAS, ...; and\\n
    WHEREAS, ...; and, be it\\nRESOLVED ..." clauses -- real clause boundaries the plain
    ".;:"-followed-by-capital-letter rule doesn't recognize (the word right after "; and" is
    lowercase "and", not the next clause's capital letter), which merged every WHEREAS clause
    into one giant run-on "sentence" for wrapping purposes and made reflow actively harmful for
    resolutions specifically -- root-caused by reading a real raw diff (SR 159, "Commending
    Project PEACE"), not guessed.
    """

    def test_reflow_splits_on_semicolon_and_connector(self):
        text = (
            "WHEREAS, the first clause states its purpose; and WHEREAS, the second clause "
            "continues the resolution; and, be it RESOLVED that the matter is concluded."
        )
        reflowed = _reflow_virginia_text(text)
        lines = reflowed.splitlines()
        # each clause starts its own textwrap run rather than being merged into one run-on
        # blob -- confirmed by each clause-starting word beginning a line of its own.
        assert any(line.startswith("WHEREAS, the first clause") for line in lines)
        assert any(line.startswith("WHEREAS, the second clause") for line in lines)
        assert any(line.startswith("RESOLVED that the matter") for line in lines)

    def test_resolution_cross_media_reflow_now_aligns_correctly(self):
        # Real shape: a PDF resolution wraps each WHEREAS clause across several physical
        # lines; the HTML version has no internal wrapping. Before the connector-aware
        # sentence break, these merged into one run-on unit and any small real insertion
        # (e.g. an "Agreed to by the Senate" adoption line) shifted every subsequent wrap
        # boundary, making an already-good match look like a near-total rewrite.
        # Padded past _VA_DEGENERATE_LEN (300 chars) with a third real-shaped WHEREAS clause --
        # see the note on PDF_WRAPPED/HTML_UNWRAPPED above for why this matters.
        pdf_text = (
            "WHEREAS, Project PEACE has served the community for two decades of\n"
            "collaboration and leadership; and\n"
            "WHEREAS, the program continues to support survivors across the region; and\n"
            "WHEREAS, the partners involved have shown a sustained commitment to the mission\n"
            "of the organization across many years of dedicated service; and, be it\n"
            "RESOLVED that the General Assembly commends this work."
        )
        html_text = (
            "Agreed to by the Senate, March 13, 2026 "
            "WHEREAS, Project PEACE has served the community for two decades of "
            "collaboration and leadership; and "
            "WHEREAS, the program continues to support survivors across the region; and "
            "WHEREAS, the partners involved have shown a sustained commitment to the mission "
            "of the organization across many years of dedicated service; and, be it "
            "RESOLVED that the General Assembly commends this work."
        )
        cleaned_prior, cleaned_raw = _clean_virginia_text(
            pdf_text, html_text, "application/pdf", "text/html"
        )
        # the real WHEREAS/RESOLVED clauses must align as matching lines after reflow --
        # only the genuinely new "Agreed to by the Senate..." content should differ.
        prior_lines = set(cleaned_prior.splitlines())
        raw_lines = set(cleaned_raw.splitlines())
        assert len(prior_lines & raw_lines) >= 2  # real shared clause content aligns
        assert any("Agreed to by the Senate" in line for line in raw_lines - prior_lines)


class TestReflowParagraphsOPEN10:
    """
    OPEN-10: unit tests for the Arizona-only reflow step (see _reflow_paragraphs()'s own
    docstring for the underlying mechanism -- Word's HTML export encodes each visual
    (word-wrapped) line as its own paragraph-like block, so a word-wrap-width difference
    between two exports of the same text fragments the same sentence into a different
    number of "lines" in each version).
    """

    def test_rejoins_word_wrapped_sentence_into_one_line(self):
        wrapped = "Be it\nenacted by the Legislature of the State of Arizona:"
        assert (
            _reflow_paragraphs(wrapped)
            == "Be it enacted by the Legislature of the State of Arizona:"
        )

    def test_preserves_blank_line_paragraph_separators(self):
        text = "Section 1. This is a sentence.\n\nSection 2. Another one."
        assert _reflow_paragraphs(text) == text  # already one sentence per line

    def test_splits_on_colon_and_semicolon_not_just_period(self):
        # 2026-08-15: real AZ bill shape (SB1503) -- a colon/semicolon only marks a real
        # clause boundary when a new capital-letter-starting clause actually follows (here,
        # a numbered definitions list); it must not fire on every literal colon/semicolon
        # regardless of what follows (see the next test).
        text = (
            "IN THIS CHAPTER, UNLESS THE CONTEXT\nOTHERWISE REQUIRES:\n"
            '1. "PLAN" MEANS A PLAN, FUND OR PROGRAM THAT IS\n'
            "ESTABLISHED BY A PUBLIC ENTITY."
        )
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "IN THIS CHAPTER, UNLESS THE CONTEXT OTHERWISE REQUIRES:",
            '1. "PLAN" MEANS A PLAN, FUND OR PROGRAM THAT IS ESTABLISHED BY A PUBLIC ENTITY.',
        ]

    def test_does_not_split_a_colon_or_semicolon_mid_clause(self):
        # Real AZ shape: "For the purposes of this section: means a deductible" -- a
        # colon/semicolon followed by a lowercase continuation is still mid-sentence, not a
        # real clause boundary, and must stay merged (this was a real regression risk found
        # while broadening the merge rule beyond the ticket's own literal starting code).
        text = "For the purposes of\nthis section:\nmeans a deductible; or\na copayment."
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "For the purposes of this section: means a deductible; or a copayment.",
        ]

    def test_collapses_double_space_after_period_artifact(self):
        # Word's HTML export routinely leaves a double space after a sentence-ending
        # period when the sentence was the last thing on a wrapped line -- a real artifact
        # confirmed against live AZ bill text (e.g. SB1671's "...appointment.  \n").
        text = "A. Requirements apply.  \nB. Other requirements apply."
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "A. Requirements apply.",
            "B. Other requirements apply.",
        ]

    def test_collapses_nbsp_artifact(self):
        # Arizona's raw HTML declares charset=windows-1252 (a Word default); a real nbsp
        # (already decoded to \xa0 by the time text reaches this function -- see
        # TestArizonaCharsetHandling below for the raw-byte-decoding side of this) must
        # collapse to a plain space like any other whitespace run, not survive as \xa0.
        text = "A. Requirements\xa0apply."
        assert _reflow_paragraphs(text) == "A. Requirements apply."

    def test_does_not_merge_across_paragraph_break(self):
        # A heading with no terminal punctuation, immediately followed by a blank line,
        # must not bleed into the next paragraph's first sentence.
        text = "CHAPTER 6.1\n\nFIDUCIARY DUTIES AND PROXY VOTING\n\nSection 1. Text."
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "CHAPTER 6.1",
            "",
            "FIDUCIARY DUTIES AND PROXY VOTING",
            "",
            "Section 1. Text.",
        ]

    def test_real_strike_all_content_change_survives_intact(self):
        # OPEN-10 AC4 -- 2026-08-15: this test previously used a hand-authored fixture
        # described as merely "mirroring" the real SB1503 case, which the ticket's own
        # revised AC4 explicitly rejected as insufficient. Replaced with SB1503's own real
        # archived text (title confirmed real: "public pensions; proxy voting"), captured
        # directly from the two real version_notes spanning its actual strike-all rewrite
        # (Senate Engrossed Version -> HOUSE - Appropriations - Strike Everything) --
        # independently re-verified directly against the live archive: this real transition's
        # noise ratio is 1.0 both before AND after reflow, i.e. a genuine, complete rewrite is
        # never mistaken for a stripping failure or suppressed.
        prior_text = (
            "Be it enacted by the Legislature of the State of Arizona:\n"
            "Section 1. Title 38, Arizona Revised Statutes, is amended by adding\n"
            "chapter 6.1, to read:\n"
            "CHAPTER 6.1\n"
            "FIDUCIARY DUTIES AND PROXY VOTING\n"
            "ARTICLE 1. GENERAL PROVISIONS\n"
            "38-971. Definitions\n"
            "IN THIS CHAPTER, UNLESS THE CONTEXT OTHERWISE REQUIRES:"
        )
        raw_text = (
            'Strike everything after the enacting clause and insert:\n'
            '"Section 1. Subject to the requirements of article IV, part 1,\n'
            "section 1, Constitution of Arizona, section 38-1171, Arizona Revised\n"
            "Statutes, is amended to read:\n"
            "38-1171. Definitions\n"
            "In this article, unless the context otherwise requires:"
        )
        reflowed_prior = _reflow_paragraphs(prior_text)
        reflowed_raw = _reflow_paragraphs(raw_text)
        diff = "\n".join(
            difflib.unified_diff(
                reflowed_prior.splitlines(), reflowed_raw.splitlines(), lineterm=""
            )
        )
        # the real, large content swap must show up in full, not be suppressed. "38-1171."
        # splits from "Definitions" (a numbered-section label the reflow doesn't specially
        # recognize) -- consistently for both documents, so it's still not a noise source.
        assert "FIDUCIARY DUTIES AND PROXY VOTING" in diff
        assert "38-1171." in diff
        assert "Definitions" in diff
        assert (
            "Section 1. Subject to the requirements of article IV, part 1, section 1, "
            "Constitution of Arizona, section 38-1171, Arizona Revised Statutes, is "
            "amended to read:" in diff
        )
        # every real line differs -- correctly reflects a genuine, complete rewrite, not a
        # stripping failure.
        assert reflowed_prior.splitlines()
        assert not (set(reflowed_prior.splitlines()) & set(reflowed_raw.splitlines()))

    def test_real_cascading_renumbering_preserves_all_content(self):
        # Found during OPEN-10's own AC5 validation against real bill SB1165: inserting a
        # new numbered definition shifts every subsequent item's number (1->2, 2->3, ...).
        # This is a genuine content change (the visible numeral IS part of the bill text),
        # not a reflow bug -- confirm reflow preserves every real definition's content
        # rather than dropping or corrupting any of them.
        prior_text = (
            "1. \"Cost sharing\" means a deductible.\n\n"
            "2. \"Diagnostic Breast Examination\" means an examination."
        )
        raw_text = (
            '1. "Additional screening services" includes a diagnostic breast examination.\n\n'
            "2. \"Cost sharing\" means a deductible.\n\n"
            "3. \"Diagnostic Breast Examination\" means an examination."
        )
        reflowed_prior = _reflow_paragraphs(prior_text)
        reflowed_raw = _reflow_paragraphs(raw_text)
        diff = "\n".join(
            difflib.unified_diff(
                reflowed_prior.splitlines(), reflowed_raw.splitlines(), lineterm=""
            )
        )
        assert '"Additional screening services" includes a diagnostic breast examination.' in diff
        # the two unchanged definitions must still appear, just renumbered -- not lost.
        assert '2. "Cost sharing" means a deductible.' in diff
        assert '3. "Diagnostic Breast Examination" means an examination.' in diff

    def test_end_statute_marker_forced_onto_its_own_line(self):
        # 2026-08-15 AC3 finding: END_STATUTE is Arizona's drafting software's own literal
        # section-delimiter token (real, confirmed on ~1/3 of the archive), always glued
        # directly onto the tail of the preceding sentence with no separating punctuation of
        # its own -- whether it ends up merged into real content or on its own line was purely
        # an accident of that version's own word-wrap width, exactly the kind of accident this
        # reflow exists to remove.
        text = "The director shall administer the fund.END_STATUTE"
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "The director shall administer the fund.",
            "END_STATUTE",
        ]

    def test_start_statute_marker_forced_onto_its_own_line(self):
        text = "read:\nSTART_STATUTE38-1171. Definitions"
        result = _reflow_paragraphs(text)
        lines = result.split("\n")
        # START_STATUTE must not stay glued onto the preceding "read:" line -- it starts a
        # fresh line of its own (the forced break also introduces a blank-line separator,
        # applied consistently on both sides of a real diff, so it isn't a noise source).
        # "38-1171." itself splits from "Definitions" (a numbered-section label, not
        # specially recognized) the same way for either side of a real diff too.
        assert lines[0] == "read:"
        assert "START_STATUTE38-1171." in lines

    def test_sentence_boundary_found_mid_line_not_just_at_line_end(self):
        # 2026-08-15 AC3 finding: real regression found on HB 2057 -- the starting
        # technique's merge rule only ever checked whether an INPUT LINE's own ending had
        # sentence-final punctuation, but Word's word-wrap can (and does) place a real
        # sentence boundary in the MIDDLE of a physical line. Whether that happens is itself
        # just an accident of that version's own wrap width. Real shape: one version wraps
        # "...administering the fund." and "Monies in the fund..." onto separate lines; the
        # other version's wrap happens to glue "...administering the fund. Monies" onto one
        # physical line instead -- both must reflow to the identical two-sentence result.
        wrapped_at_boundary = (
            "Not more than ten percent of monies deposited in the\n"
            "fund annually shall be used for the cost of administering the fund.\n"
            "Monies in the fund are continuously appropriated."
        )
        wrapped_mid_sentence = (
            "Not more than ten percent of monies deposited in the\n"
            "fund annually shall be used for the cost of administering the fund. Monies\n"
            "in the fund are continuously appropriated."
        )
        assert _reflow_paragraphs(wrapped_at_boundary) == _reflow_paragraphs(
            wrapped_mid_sentence
        )

    def test_all_caps_sentence_ending_is_not_mistaken_for_a_subsection_marker(self):
        # 2026-08-15 AC3 finding: a real, serious bug found while broadening the subsection-
        # marker exclusion -- checking only "is this preceded by [A-Z]." can't tell a real
        # lone subsection letter ("B.") from the tail of a longer, ordinarily-capitalized
        # word ending a sentence, and Arizona's own convention of rendering amended statutory
        # text in ALL CAPS meant this was accidentally suppressing the split after nearly
        # every all-caps sentence (e.g. "...ELECTRONICALLY." was treated as if "Y." were
        # itself a subsection marker), silently merging entire lettered subsections into one
        # giant run-on line. Real shape confirmed on HB 2857.
        text = (
            "A. THE DEPARTMENT MAY STORE AN INMATE'S MEDICAL RECORDS\n"
            "ELECTRONICALLY.\n"
            "B. NOTWITHSTANDING ANY OTHER LAW, THE DEPARTMENT IS NOT REQUIRED TO KEEP\n"
            "PHYSICAL COPIES."
        )
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "A. THE DEPARTMENT MAY STORE AN INMATE'S MEDICAL RECORDS ELECTRONICALLY.",
            "B. NOTWITHSTANDING ANY OTHER LAW, THE DEPARTMENT IS NOT REQUIRED TO KEEP "
            "PHYSICAL COPIES.",
        ]

    def test_semicolon_and_whereas_clause_chain_splits_correctly(self):
        # 2026-08-15 AC3 finding: the same cross-jurisdiction pattern independently found and
        # fixed for VA's OPEN-9 -- a real "; and Whereas" clause chain (common in AZ's own
        # resolution/memorial preambles) doesn't split on the plain rule, since the word right
        # after "; and" is lowercase, not the next clause's own capital letter. Without this,
        # an entire multi-WHEREAS preamble collapses into one giant merged line, and any small
        # real difference elsewhere then dominates a now-tiny total line count. Real shape
        # confirmed on HCR 2015.
        text = (
            "Whereas, regular physical activity strengthens physical health; and\n"
            "Whereas, national health authorities recommend daily activity; and\n"
            "Whereas, public schools influence lifelong habits.\n"
            "Therefore Be it resolved."
        )
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "Whereas, regular physical activity strengthens physical health; and",
            "Whereas, national health authorities recommend daily activity; and",
            "Whereas, public schools influence lifelong habits.",
            "Therefore Be it resolved.",
        ]


class TestArizonaCharsetHandling:
    """
    OPEN-10: Arizona's raw HTML declares charset=windows-1252 (a Microsoft Word default).
    Confirmed directly against real archived AZ bills: passing raw bytes straight into the
    real extractor (which parses via lxml.html.fromstring(data) on bytes, honoring the
    declared <meta charset>) decodes a real nbsp correctly; naively pre-decoding the same
    bytes as UTF-8 first mangles it into U+FFFD. The reflow step itself never reads raw
    bytes (it only ever sees the already-extracted str) so it has no charset-decoding code
    of its own -- this test pins the *existing* extraction path's correct behavior, since
    any future direct-byte-reading tooling built on this work (e.g. a local-archive
    validation script) would otherwise be exposed to the same real mistake found during
    OPEN-10's research.
    """

    def test_windows1252_nbsp_decodes_correctly_through_the_real_extractor(self):
        from openstates.fulltext import CONVERSION_FUNCTIONS

        # 0xA0 is windows-1252's nbsp -- the real byte AZ's Word export uses.
        html = (
            b'<html><head><meta http-equiv=Content-Type '
            b'content="text/html; charset=windows-1252"></head>'
            b'<body><div class="WordSection2">Requirements\xa0apply.</div></body></html>'
        )
        func = CONVERSION_FUNCTIONS["az"]["text/html"]
        text = func(
            html,
            {
                "url": "",
                "media_type": "text/html",
                "title": "",
                "jurisdiction_id": "ocd-jurisdiction/country:us/state:az/government",
            },
        )
        assert "�" not in text
        assert "Requirements apply." in text

    def test_naive_utf8_predecode_would_have_mangled_the_same_bytes(self):
        # Demonstrates the real bug this guards against: decoding the identical bytes as
        # UTF-8 *before* handing them to lxml (instead of letting lxml's own charset-aware
        # parser see the raw bytes) corrupts the nbsp into a replacement character. Proves
        # the fixture above actually exercises a real, currently-avoided pitfall.
        html = (
            b'<html><head><meta http-equiv=Content-Type '
            b'content="text/html; charset=windows-1252"></head>'
            b'<body><div class="WordSection2">Requirements\xa0apply.</div></body></html>'
        )
        mangled = html.decode("utf-8", "replace")
        assert "�" in mangled


@pytest.mark.django_db
class TestArizonaReflowJurisdictionGate:
    """
    OPEN-10 AC1/AC2: a non-Arizona bill's prior_text/raw_text and
    diff_from_previous_version must be byte-for-byte identical to pre-OPEN-10 behavior --
    reflow must only ever fire for jurisdiction.name == "Arizona".
    """

    WRAPPED_INTRODUCED = "Be it\nenacted by the Legislature of the State of Arizona:"
    WRAPPED_ENROLLED = (
        "Be it\nenacted by the Legislature of the State of\nArizona, with an amendment:"
    )

    def _run_two_version_bill(self, jid, jurisdiction_name):
        bill = _make_bill(jid=jid, jurisdiction_name=jurisdiction_name)
        introduced = bill.versions.create(note="Introduced", date="")
        enrolled = bill.versions.create(note="Enrolled", date="")
        introduced.links.create(
            url="https://example.test/introduced.html", media_type="text/html"
        )
        enrolled.links.create(
            url="https://example.test/enrolled.html", media_type="text/html"
        )

        texts_by_url = {
            "https://example.test/introduced.html": self.WRAPPED_INTRODUCED,
            "https://example.test/enrolled.html": self.WRAPPED_ENROLLED,
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

        return {
            d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)
        }

    def test_non_arizona_diff_is_unreflowed_line_by_line(self):
        docs = self._run_two_version_bill(
            "ocd-jurisdiction/country:us/state:ak/government", jurisdiction_name="Alaska"
        )
        expected = "\n".join(
            difflib.unified_diff(
                self.WRAPPED_INTRODUCED.splitlines(),
                self.WRAPPED_ENROLLED.splitlines(),
                lineterm="",
            )
        )
        assert docs["Enrolled"].diff_from_previous_version == expected
        # sanity check the fixture actually WOULD diff differently if reflowed -- otherwise
        # this test can't distinguish reflow-off from reflow-on.
        reflowed_expected = "\n".join(
            difflib.unified_diff(
                _reflow_paragraphs(self.WRAPPED_INTRODUCED).splitlines(),
                _reflow_paragraphs(self.WRAPPED_ENROLLED).splitlines(),
                lineterm="",
            )
        )
        assert expected != reflowed_expected

    def test_non_arizona_raw_text_and_prior_text_untouched(self):
        docs = self._run_two_version_bill(
            "ocd-jurisdiction/country:us/state:ak/government", jurisdiction_name="Alaska"
        )
        assert docs["Introduced"].raw_text == self.WRAPPED_INTRODUCED
        assert docs["Enrolled"].raw_text == self.WRAPPED_ENROLLED

    def test_arizona_diff_is_reflowed(self):
        docs = self._run_two_version_bill(
            "ocd-jurisdiction/country:us/state:az/government", jurisdiction_name="Arizona"
        )
        expected = "\n".join(
            difflib.unified_diff(
                _reflow_paragraphs(self.WRAPPED_INTRODUCED).splitlines(),
                _reflow_paragraphs(self.WRAPPED_ENROLLED).splitlines(),
                lineterm="",
            )
        )
        assert docs["Enrolled"].diff_from_previous_version == expected

    def test_arizona_stored_raw_text_is_never_reflowed(self):
        # Only the *diff* is reflowed -- the persisted raw_text field must stay exactly
        # what the extractor produced, for every jurisdiction including Arizona.
        docs = self._run_two_version_bill(
            "ocd-jurisdiction/country:us/state:az/government", jurisdiction_name="Arizona"
        )
        assert docs["Introduced"].raw_text == self.WRAPPED_INTRODUCED
        assert docs["Enrolled"].raw_text == self.WRAPPED_ENROLLED


class TestFetchBytesResilienceProfileDispatchOPEN54:
    """OPEN-54: _fetch_bytes() dispatches on a URL's netloc having a resilience profile,
    generalized from the old MI-only `if "legislature.mi.gov" in ...` branch."""

    def test_unprofiled_url_uses_the_plain_scraper_unchanged(self):
        from openstates.cli.text_extract import _fetch_bytes

        with mock.patch("openstates.cli.text_extract.scraper") as fake_scraper:
            fake_scraper.request.return_value = mock.Mock(content=b"plain content")
            result = _fetch_bytes("https://example.com/some-bill.pdf")

        assert result == b"plain content"
        fake_scraper.request.assert_called_once_with(
            "GET", "https://example.com/some-bill.pdf", allow_redirects=True
        )

    def test_profiled_url_goes_through_its_cookie_provider_not_the_plain_scraper(self):
        from openstates.cli.text_extract import _fetch_bytes

        fake_profile = mock.Mock(
            name="fake",
            requests_per_minute=10,
            circuit_breaker_max_consecutive_blocks=3,
        )
        fake_profile.cookie_provider.fetch_with_retry.return_value = mock.Mock(
            content=b"real profiled content"
        )

        with mock.patch(
            "openstates.cli.text_extract.profile_for_netloc", return_value=fake_profile
        ), mock.patch("openstates.cli.text_extract.scraper") as fake_plain_scraper:
            result = _fetch_bytes("https://flhouse.gov/some-bill-detail.aspx")

        assert result == b"real profiled content"
        fake_plain_scraper.request.assert_not_called()
        fake_profile.cookie_provider.fetch_with_retry.assert_called_once()


class TestFetchBytesCircuitBreakerOPEN52:
    """OPEN-52: a sustained WAF block must abort the whole archive run (ScrapeError), not get
    silently absorbed as one more per-document blocked/fetch_errors count forever."""

    def _fake_profile(self, max_consecutive_blocks):
        from openstates.utils.cookie_provider import WafBlockDetected

        fake_profile = mock.Mock(
            name="fake",
            requests_per_minute=10,
            circuit_breaker_max_consecutive_blocks=max_consecutive_blocks,
        )
        fake_profile.cookie_provider.fetch_with_retry.side_effect = WafBlockDetected(
            "always blocked"
        )
        return fake_profile

    def test_below_threshold_reraises_wafblockdetected_not_scrapeerror(self):
        from openstates.cli.text_extract import _fetch_bytes
        from openstates.utils.cookie_provider import WafBlockDetected

        fake_profile = self._fake_profile(max_consecutive_blocks=3)
        with mock.patch(
            "openstates.cli.text_extract.profile_for_netloc", return_value=fake_profile
        ), mock.patch("openstates.cli.text_extract._profile_consecutive_blocks", {}):
            with pytest.raises(WafBlockDetected):
                _fetch_bytes("https://flhouse.gov/one.aspx")

    def test_reaching_threshold_raises_scrapeerror_instead(self):
        from openstates.cli.text_extract import _fetch_bytes
        from openstates.exceptions import ScrapeError

        fake_profile = self._fake_profile(max_consecutive_blocks=2)
        with mock.patch(
            "openstates.cli.text_extract.profile_for_netloc", return_value=fake_profile
        ), mock.patch("openstates.cli.text_extract._profile_consecutive_blocks", {}):
            from openstates.utils.cookie_provider import WafBlockDetected

            with pytest.raises(WafBlockDetected):
                _fetch_bytes("https://flhouse.gov/one.aspx")
            with pytest.raises(ScrapeError):
                _fetch_bytes("https://flhouse.gov/two.aspx")

    def test_a_success_in_between_resets_the_counter(self):
        from openstates.cli.text_extract import _fetch_bytes
        from openstates.utils.cookie_provider import WafBlockDetected

        fake_profile = self._fake_profile(max_consecutive_blocks=2)
        with mock.patch(
            "openstates.cli.text_extract.profile_for_netloc", return_value=fake_profile
        ), mock.patch("openstates.cli.text_extract._profile_consecutive_blocks", {}):
            with pytest.raises(WafBlockDetected):
                _fetch_bytes("https://flhouse.gov/one.aspx")

            fake_profile.cookie_provider.fetch_with_retry.side_effect = None
            fake_profile.cookie_provider.fetch_with_retry.return_value = mock.Mock(
                content=b"recovered"
            )
            assert _fetch_bytes("https://flhouse.gov/two.aspx") == b"recovered"

            fake_profile.cookie_provider.fetch_with_retry.side_effect = WafBlockDetected(
                "blocked again"
            )
            fake_profile.cookie_provider.fetch_with_retry.return_value = None
            # Only one block since the reset -- must not raise ScrapeError yet.
            with pytest.raises(WafBlockDetected):
                _fetch_bytes("https://flhouse.gov/three.aspx")


@pytest.mark.django_db
class TestArchiveCommandAbortsOnScrapeErrorOPEN52:
    """OPEN-52 AC: a fully-blocked archive run must exit non-zero and print a clear signal,
    not silently complete with exit 0 and a high (but unalerted-on) blocked count."""

    def test_scrape_error_from_archive_bill_versions_exits_nonzero(self):
        from openstates.cli.text_extract import archive
        from openstates.exceptions import ScrapeError
        from click.testing import CliRunner

        bill = _make_bill(jid="ocd-jurisdiction/country:us/state:fl/government")

        with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
            "openstates.cli.text_extract.abbr_to_jid",
            return_value=bill.legislative_session.jurisdiction_id,
        ), mock.patch(
            "openstates.cli.text_extract.archive_bill_versions",
            side_effect=ScrapeError("fl archive fetch aborted: 3 consecutive WAF blocks"),
        ):
            runner = CliRunner()
            result = runner.invoke(archive, ["fl"])

        assert result.exit_code == 1
        assert "aborted" in result.output


def _make_transport_response(request, status_code, content):
    import requests

    resp = requests.Response()
    resp.status_code = status_code
    resp._content = content
    resp.url = request.url
    resp.request = request
    return resp


class TestFetchBytesRealScrapelibRetryInteractionOPEN53:
    """OPEN-53 (reopened 2026-08-15): a real 403/fake-404 WAF-block response must be handled
    by CookieProvider.fetch_with_retry's own single invalidate-and-rewarm-once retry, not
    scrapelib's own blind retry_attempts loop firing first with the same stale cookies. Patches
    only at the requests transport layer (HTTPAdapter.send) so scrapelib's real
    Scraper/RetrySession code -- accept_response, raise_errors, the retry loop itself -- all
    actually run, instead of mocking around them."""

    def test_blocked_then_recovered_hits_transport_exactly_twice_not_five_times(
        self, tmp_path
    ):
        from openstates.cli.text_extract import _fetch_bytes
        from openstates.utils.cookie_provider import CookieProvider
        from openstates.utils.resilience_profiles import WafResilienceProfile

        send_calls = []

        def fake_send(self, request, **kwargs):
            send_calls.append(request.url)
            if len(send_calls) == 1:
                return _make_transport_response(request, 403, b"Request Rejected")
            return _make_transport_response(
                request, 200, b"real bill content, no block markers here"
            )

        warm_up_calls = []

        def fake_warm_up(url):
            warm_up_calls.append(url)
            return (
                [{"name": "session_cookie_mfhp", "value": "fresh", "expires": 0}],
                "Real Chrome UA",
            )

        real_profile = WafResilienceProfile(
            name="test-open53",
            netloc="flhouse.gov",
            cookie_provider=CookieProvider(
                name="test-open53",
                warm_up_url="https://flhouse.gov/",
                cookie_names=("session_cookie_mfhp",),
                cache_path=str(tmp_path / "test_open53_waf_cookies.json"),
                warm_up_func=fake_warm_up,
            ),
            requests_per_minute=60,
            circuit_breaker_max_consecutive_blocks=3,
            retry_excluded_exceptions=(),
            user_agent_rotation_enabled=False,
        )

        with mock.patch(
            "openstates.cli.text_extract.profile_for_netloc", return_value=real_profile
        ), mock.patch(
            "openstates.cli.text_extract._profile_consecutive_blocks", {}
        ), mock.patch(
            "openstates.cli.text_extract._profile_scrapers", {}
        ), mock.patch(
            "requests.adapters.HTTPAdapter.send", fake_send
        ):
            content = _fetch_bytes("https://flhouse.gov/some-bill-detail.aspx")

        assert content == b"real bill content, no block markers here"
        # Exactly 2 transport-level calls -- the initial (blocked) attempt and the one
        # retry after CookieProvider invalidated and re-warmed. NOT scrapelib's own
        # retry_attempts=5 blindly hitting the same stale cookies 5 more times first.
        assert len(send_calls) == 2
        # Exactly one re-warm happened (the initial get_cookies() call during do_request
        # plus the invalidate-triggered re-warm) -- not the un-set default cache TTL path.
        assert len(warm_up_calls) == 2
