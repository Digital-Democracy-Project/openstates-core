"""OPEN-219: the two diff-computing paths must produce identical output.

`archive_bill_versions` applied four per-jurisdiction pre-diff cleaners (OPEN-7 WA, OPEN-9
VA, OPEN-10 AZ, OPEN-11 MI); `recomputed_diffs_for_documents` applied none. The same bill
through the two paths produced two different diffs, and nothing indicated which you had.

No individual commit was wrong. Each cleaner ticket was scoped as "clean this
jurisdiction's text before diffing in archive_bill_versions" and did exactly that. The gap
is that a second diff path had appeared eight days earlier, ~600 lines away in the same
file, and **nothing compared them**. That missing comparison is what this module is.

The assertion is deliberately shaped as: archive a bill, then recompute it, and require the
recompute to report NOTHING changed. `recomputed_diffs_for_documents` already reports a
document as "changed" when its recomputed diff differs from the stored one, so "changed is
empty" is precisely "the two paths agree" -- no need to reimplement either side to compare
them.
"""

from unittest import mock

import pytest

from openstates.cli.text_extract import (
    apply_prediff_cleaning,
    archive_bill_versions,
    recomputed_diffs_for_documents,
)
from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_text_extract import _make_bill


# Per-jurisdiction fixtures. Each pair must contain material its own cleaner actually
# removes, or the parity assertion passes vacuously -- `test_the_fixtures_are_not_vacuous`
# below enforces that, so a future edit cannot quietly hollow these out.
FIXTURES = {
    "Washington": (
        "ocd-jurisdiction/country:us/state:wa/government",
        "Z-0236.3  SUBSTITUTE SENATE BILL 5167\n"
        "State of Washington 69th Legislature 2025 Regular Session\n"
        "By Senate Ways & Means (originally sponsored by Senators Smith and Jones)\n"
        "Read first time 01/13/25.  Referred to Committee on Ways & Means.\n"
        "AN ACT Relating to alimony; amending RCW 26.09.090; and creating a new section.\n"
        "1  Sec. 1.  A court may consider the tax consequences of an award.\n"
        "2  Sec. 2.  This act takes effect July 1, 2025.\n",
        "Z-0236.4  SUBSTITUTE SENATE BILL 5167\n"
        "State of Washington 69th Legislature 2025 Regular Session\n"
        "By Senate Ways & Means (originally sponsored by Senators Smith and Jones)\n"
        "Read first time 01/14/25.  Referred to Committee on Ways & Means.\n"
        "AN ACT Relating to alimony; amending RCW 26.09.090; and creating a new section.\n"
        "1  Sec. 1.  A court shall consider the tax consequences of an award.\n"
        "2  Sec. 2.  This act takes effect July 1, 2025.\n",
    ),
    "Michigan": (
        "ocd-jurisdiction/country:us/state:mi/government",
        "SENATE BILL NO. 542\n\nA bill to amend 2014 PA 259.\n\n"
        "the people of the state of michigan enact:\n\n"
        "1   Sec. 3. (1) The Michigan National Guard tuition assistance program\n"
        "2   is created within  the department of military and veterans affairs.\n",
        "SENATE BILL NO. 542\n\nA bill to amend 2014 PA 259.\n\n"
        "the people of the state of michigan enact:\n\n"
        "1   Sec. 3. (1) The Michigan Army National Guard tuition assistance program\n"
        "2   is created within  the department of military and veterans affairs.\n",
    ),
    # _clean_virginia_text has a degenerate-extraction guard at 300 characters -- below it
    # both sides are returned untouched and the fixture would prove nothing.
    "Virginia": (
        "ocd-jurisdiction/country:us/state:va/government",
        "2025 SESSION\nINTRODUCED\n"
        "  1  A BILL to amend and reenact section 58.1-3506 of the Code of Virginia,\n"
        "  2  relating to the classification of certain tangible personal property for\n"
        "  3  purposes of local taxation, and to provide for an effective date and for\n"
        "  4  the continued operation of existing local ordinances adopted thereunder.\n"
        "Referred to Committee on Finance and Appropriations\n"
        "  5  Be it enacted by the General Assembly of Virginia that a court may\n"
        "  6  consider the tax consequences of an award of spousal support.              SJ58\n",
        "2025 SESSION\nENROLLED\n"
        "  1  A BILL to amend and reenact section 58.1-3506 of the Code of Virginia,\n"
        "  2  relating to the classification of certain tangible personal property for\n"
        "  3  purposes of local taxation, and to provide for an effective date and for\n"
        "  4  the continued operation of existing local ordinances adopted thereunder.\n"
        "Referred to Committee on Finance and Appropriations\n"
        "  5  Be it enacted by the General Assembly of Virginia that a court shall\n"
        "  6  consider the tax consequences of an award of spousal support.              SJ58\n",
    ),
    "Arizona": (
        "ocd-jurisdiction/country:us/state:az/government",
        "Be it enacted by the Legislature of the State of\nArizona:\n"
        "Section 1.  A court may consider the tax\nconsequences of an award.\n"
        "Sec. 2.  This act is effective from and after\nDecember 31, 2025.\n",
        "Be it enacted by the Legislature of the State of\nArizona:\n"
        "Section 1.  A court shall consider the tax\nconsequences of an award.\n"
        "Sec. 2.  This act is effective from and after\nDecember 31, 2025.\n",
    ),
    # The control. No cleaner exists, so the two paths agreed even before this fix -- which
    # is exactly why the divergence went unnoticed for two weeks: most traffic is here.
    "Test": (
        "ocd-jurisdiction/country:us/state:ak/government",
        "SECTION 1\nA court may consider the tax consequences.\nSECTION 2\nEffective July 1.",
        "SECTION 1\nA court shall consider the tax consequences.\nSECTION 2\nEffective July 1.",
    ),
}

V1_URL = "https://x.test/v1.pdf"
V2_URL = "https://x.test/v2.pdf"


def _archive(bill, texts):
    def fake_fetch(url):
        return texts[url].encode("utf-8")

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


def _build_and_archive(jurisdiction):
    jid, v1_text, v2_text = FIXTURES[jurisdiction]
    bill = _make_bill(jid=jid, jurisdiction_name=jurisdiction)
    # Michigan gates its line-number and whitespace normalisation to Bill-classified notes
    # only (OPEN-11), so the fixture has to be a bill for that half to run at all.
    bill.classification = ["bill"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=V1_URL, media_type="application/pdf")
    v2 = bill.versions.create(note="Enrolled", date="")
    v2.links.create(url=V2_URL, media_type="application/pdf")
    _archive(bill, {V1_URL: v1_text, V2_URL: v2_text})
    return bill


@pytest.mark.django_db
@pytest.mark.parametrize("jurisdiction", sorted(FIXTURES))
def test_both_diff_paths_agree(jurisdiction):
    """AC2, and the whole ticket in one assertion.

    On main this fails for Washington, Michigan, Virginia and Arizona -- the recompute path
    reports every archived diff as "changed" because it recomputes them without the cleaning
    the archiver applied. It passes for the no-cleaner control both before and after.
    """
    bill = _build_and_archive(jurisdiction)
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))

    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name=jurisdiction, is_bill=True
    )

    assert result["changed"] == [], (
        f"{jurisdiction}: recompute disagrees with the archiver for "
        f"{len(result['changed'])} document(s)"
    )
    assert len(result["unchanged"]) == len(docs)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "jurisdiction", [j for j in sorted(FIXTURES) if j != "Test"]
)
def test_the_fixtures_are_not_vacuous(jurisdiction):
    """Guard against the parity test above passing for the wrong reason.

    If a fixture stopped containing anything its own cleaner removes, cleaning would be a
    no-op and both paths would agree trivially -- the test would still be green while
    testing nothing. Assert the cleaner actually changes each fixture's text."""
    _, v1_text, v2_text = FIXTURES[jurisdiction]
    cleaned_prior, cleaned_raw = apply_prediff_cleaning(
        v1_text,
        v2_text,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert (cleaned_prior, cleaned_raw) != (v1_text, v2_text), (
        f"{jurisdiction}: the fixture contains nothing its cleaner removes, so the parity "
        f"test would pass vacuously"
    )


@pytest.mark.django_db
def test_a_jurisdiction_with_no_cleaner_is_untouched():
    """Every jurisdiction without a cleaner must be byte-for-byte unchanged by this ticket
    (AC4). Stated against the shared helper directly, so it holds for any caller."""
    text_a, text_b = "line one\nline two", "line one\nline three"
    for jurisdiction in ("Test", "United States", "Utah", "Florida", "Massachusetts", "Alabama"):
        assert apply_prediff_cleaning(
            text_a,
            text_b,
            jurisdiction_name=jurisdiction,
            is_bill=True,
            prior_media_type="application/pdf",
            cur_media_type="application/pdf",
        ) == (text_a, text_b), jurisdiction


@pytest.mark.django_db
def test_michigan_line_number_stripping_still_respects_the_resolution_gate():
    """OPEN-11 deliberately applies Michigan's line-number and whitespace normalisation to
    Bill-classified notes only, since Resolutions have different conventions those steps
    distort. Threading `is_bill` through the shared helper must preserve that."""
    _, v1_text, _ = FIXTURES["Michigan"]
    as_bill, _ = apply_prediff_cleaning(
        v1_text, v1_text, jurisdiction_name="Michigan", is_bill=True,
        prior_media_type="application/pdf", cur_media_type="application/pdf",
    )
    as_resolution, _ = apply_prediff_cleaning(
        v1_text, v1_text, jurisdiction_name="Michigan", is_bill=False,
        prior_media_type="application/pdf", cur_media_type="application/pdf",
    )
    assert as_bill != as_resolution
    # the leading margin line numbers survive on the resolution path, and not on the bill one
    assert any(ln.startswith("1   Sec.") for ln in as_resolution.splitlines())
    assert not any(ln.startswith("1   Sec.") for ln in as_bill.splitlines())


# --- /pm-review round 1 -------------------------------------------------------------
#
# The parity test above calls recomputed_diffs_for_documents DIRECTLY, supplying
# jurisdiction_name and is_bill by hand. Round 1 correctly objected that this proves the
# shared helper behaves, but NOT that production derives the same arguments -- a wiring
# error in recompute_bill_diff_order would survive it. These enter through the production
# wrapper instead, deriving everything from the bill exactly as the real command does.


@pytest.mark.django_db
@pytest.mark.parametrize("jurisdiction", sorted(FIXTURES))
def test_both_paths_agree_through_the_production_wrapper(jurisdiction):
    """Same assertion as `test_both_diff_paths_agree`, but nothing is passed by hand:
    `recompute_bill_diff_order` derives jurisdiction and classification off the bill."""
    from openstates.cli.text_extract import recompute_bill_diff_order

    bill = _build_and_archive(jurisdiction)
    assert recompute_bill_diff_order(bill)["changed"] == [], (
        f"{jurisdiction}: the production recompute wrapper disagrees with the archiver"
    )


@pytest.mark.django_db
def test_the_wrapper_derives_is_bill_the_same_way_the_archiver_does():
    """Round 1: 'Archive and recompute may calculate is_bill differently.' They did --
    the archive path ANDed the jurisdiction into it (`is_michigan and classification ==
    ["bill"]`) while the wrapper used classification alone. Harmless, since only Michigan's
    cleaner reads the value, but a real argument-parity gap. Both now use the bill's own
    classification, and a Michigan RESOLUTION is where a divergence would show, because
    that is the one input whose cleaning depends on it."""
    from openstates.cli.text_extract import recompute_bill_diff_order

    jid, v1_text, v2_text = FIXTURES["Michigan"]
    bill = _make_bill(jid=jid, jurisdiction_name="Michigan")
    bill.classification = ["resolution"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=V1_URL, media_type="application/pdf")
    v2 = bill.versions.create(note="Enrolled", date="")
    v2.links.create(url=V2_URL, media_type="application/pdf")
    _archive(bill, {V1_URL: v1_text, V2_URL: v2_text})

    assert recompute_bill_diff_order(bill)["changed"] == []


@pytest.mark.django_db
def test_parity_holds_when_a_version_carries_two_media_types():
    """Round 1: does the recompute path really partition predecessors by media type before
    passing `doc.media_type` for both sides? It does -- `prior_by_media.get(doc.media_type)`
    (OPEN-211/OPEN-217) -- but that was asserted in prose, not shown. A mixed-media fixture
    proves the two paths still agree when a version has both a PDF and an HTML rendering,
    which is when a same-media baseline mistake would surface."""
    from openstates.cli.text_extract import recompute_bill_diff_order

    jid, v1_text, v2_text = FIXTURES["Washington"]
    bill = _make_bill(jid=jid, jurisdiction_name="Washington")
    bill.classification = ["bill"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=V1_URL, media_type="application/pdf")
    v1.links.create(url="https://x.test/v1.htm", media_type="text/html")
    v2 = bill.versions.create(note="Enrolled", date="")
    v2.links.create(url=V2_URL, media_type="application/pdf")
    v2.links.create(url="https://x.test/v2.htm", media_type="text/html")

    _archive(
        bill,
        {
            V1_URL: v1_text,
            V2_URL: v2_text,
            # deliberately different text per rendering, so a cross-media baseline would
            # produce a visibly different diff rather than coincidentally matching
            "https://x.test/v1.htm": v1_text.replace("Sec. 1.", "Section 1."),
            "https://x.test/v2.htm": v2_text.replace("Sec. 1.", "Section 1."),
        },
    )

    assert recompute_bill_diff_order(bill)["changed"] == []
    assert BillVersionDocument.objects.filter(bill=bill).count() == 4


@pytest.mark.django_db
def test_error_and_empty_documents_never_reach_the_cleaner():
    """Round 1: 'Are error and missing-text rows excluded before apply_prediff_cleaning?'

    Yes -- the `prior_text is not None and not doc.is_error and doc.raw_text` guard sits in
    front of the call on both paths, so a cleaner never sees an error row or empty text.
    Asserted by spying on the helper rather than by reading the guard, so a future edit that
    moves the call above the guard fails here."""
    from openstates.cli import text_extract as te

    jid, v1_text, v2_text = FIXTURES["Washington"]
    bill = _make_bill(jid=jid, jurisdiction_name="Washington")
    bill.classification = ["bill"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=V1_URL, media_type="application/pdf")
    v2 = bill.versions.create(note="Enrolled", date="")
    v2.links.create(url=V2_URL, media_type="application/pdf")
    # v1 extracts to nothing -> is_error, so v2 has no usable predecessor at all
    _archive(bill, {V1_URL: "", V2_URL: v2_text})

    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    assert any(d.is_error for d in docs)

    seen = []
    real = te.apply_prediff_cleaning
    with mock.patch.object(
        te, "apply_prediff_cleaning",
        side_effect=lambda p, r, **kw: (seen.append((p, r)), real(p, r, **kw))[1],
    ):
        te.recomputed_diffs_for_documents(
            docs, jurisdiction_name="Washington", is_bill=True
        )

    assert seen == [], f"cleaner was called with {seen!r} despite an error/empty row"


@pytest.mark.django_db
def test_an_unknown_stage_version_is_excluded_on_both_paths():
    """A version whose note matches no known stage takes no diff and becomes no baseline
    (OPEN-34). Pinned here too because cleaning now sits inside that same branch."""
    from openstates.cli.text_extract import recompute_bill_diff_order

    jid, v1_text, v2_text = FIXTURES["Virginia"]
    bill = _make_bill(jid=jid, jurisdiction_name="Virginia")
    bill.classification = ["bill"]
    bill.save()
    v1 = bill.versions.create(note="Introduced", date="")
    v1.links.create(url=V1_URL, media_type="application/pdf")
    mystery = bill.versions.create(note="???", date="")
    mystery.links.create(url=V2_URL, media_type="application/pdf")

    _archive(bill, {V1_URL: v1_text, V2_URL: v2_text})

    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert docs["???"].diff_from_previous_version is None
    assert recompute_bill_diff_order(bill)["changed"] == []
