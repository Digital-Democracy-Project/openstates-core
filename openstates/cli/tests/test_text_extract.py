import difflib
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
    archive_bill_versions,
    recompute_bill_diff_order,
    _reextract_document,
    S3_BILL_ARCHIVE_BUCKET,
)


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


def _make_bill(jid="ocd-jurisdiction/country:us/state:ak/government", jurisdiction_name="Test"):
    Division.objects.get_or_create(
        id="ocd-division/country:us", defaults={"name": "USA"}
    )
    j, _ = Jurisdiction.objects.get_or_create(
        id=jid,
        defaults={"division_id": "ocd-division/country:us", "name": jurisdiction_name},
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

        doc.refresh_from_db()
        assert doc.is_error is True  # untouched


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
        text = "For the purposes of\nthis section:\nmeans a deductible; or\na copayment."
        result = _reflow_paragraphs(text)
        assert result.split("\n") == [
            "For the purposes of this section:",
            "means a deductible; or a copayment.",
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
        # OPEN-10 AC4: mirrors the real SB1503 case -- a genuine "strike everything"
        # rewrite where the entire subject matter changes between versions. Reflow must
        # not delete, truncate, or otherwise obscure content that's simply *very
        # different* from the prior version -- it only rejoins wrapped fragments.
        prior_text = (
            "Section 1. Title 38, Arizona Revised Statutes,\n"
            "is amended by adding chapter 6.1, to read:\n\n"
            "CHAPTER 6.1\n\nFIDUCIARY DUTIES AND PROXY VOTING"
        )
        raw_text = (
            "Section 1. Subject to the requirements of\n"
            "article IV, part 1, section 1, Constitution of Arizona, section 38-1171,\n"
            "Arizona Revised Statutes, is amended to read:\n\n"
            "START_STATUTE38-1171. Definitions"
        )
        reflowed_prior = _reflow_paragraphs(prior_text)
        reflowed_raw = _reflow_paragraphs(raw_text)
        diff = "\n".join(
            difflib.unified_diff(
                reflowed_prior.splitlines(), reflowed_raw.splitlines(), lineterm=""
            )
        )
        # the real, large content swap must show up in full, not be suppressed
        assert "FIDUCIARY DUTIES AND PROXY VOTING" in diff
        assert "START_STATUTE38-1171. Definitions" in diff
        assert (
            "Section 1. Subject to the requirements of article IV, part 1, section 1, "
            "Constitution of Arizona, section 38-1171, Arizona Revised Statutes, is "
            "amended to read:" in diff
        )

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
