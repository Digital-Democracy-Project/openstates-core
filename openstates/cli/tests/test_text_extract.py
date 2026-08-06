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
    archive_bill_versions,
    recompute_bill_diff_order,
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
