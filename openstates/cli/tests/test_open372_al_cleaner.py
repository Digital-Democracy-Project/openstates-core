"""OPEN-372: Alabama's pre-diff cleaner.

Alabama's "Introduced" carries a SYNOPSIS block its later versions do not, every version has a
different tracking code on line 2, the title block changes shape ("A BILL / TO BE ENTITLED / AN
ACT" becomes "Enrolled, An Act,"), and every "Engrossed" ends with a legislative-history block.
The fixtures have the shapes measured on the production replica (HB249).
"""

import pytest

from openstates.cli.text_extract import (
    _clean_al_text,
    apply_prediff_cleaning,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

AL = "ocd-jurisdiction/country:us/state:al/government"

HEADER = "HB249\n{track}\nBy Representative Faulkner\nRFD: Judiciary\nFirst Read: 15-Jan-26\n"

INTRODUCED = (
    HEADER.format(track="JPKGJNN-1") + "SYNOPSIS:\n"
    "Under existing law, a proceeding involving a\n"
    "guardianship may be removed to the circuit court.\n"
    "This bill would provide additional procedures.\n"
    "A BILL\n"
    "TO BE ENTITLED\n"
    "AN ACT\n"
    "Relating to guardianships and conservatorships.\n"
    "Section 1. A court may remove a proceeding.\n"
    "Section 2. This act is effective on October 1,\n"
    "2026.\n"
)

ENGROSSED = (
    HEADER.format(track="DGC49NN-2") + "A BILL\n"
    "TO BE ENTITLED\n"
    "AN ACT\n"
    "Relating to guardianships and conservatorships.\n"
    "Section 1. A court shall remove a proceeding.\n"
    "Section 2. This act is effective on October 1,\n"
    "2026.\n"
    "House of Representatives\n"
    "Read for the first time and referred ................05-Feb-26\n"
    "to the House of Representatives\n"
    "committee on Economic Development\n"
    "Read for the second time and placed   ................12-Feb-26\n"
    "on the calendar:\n"
    "Read for the third time and passed    ................26-Feb-26\n"
    "as amended\n"
    "Yeas 93\n"
    "Nays 0\n"
    "Abstains 11\n"
    "John Treadwell\n"
    "Clerk\n"
)

ENROLLED = (
    HEADER.format(track="DGC49NN-3") + "Enrolled, An Act,\n"
    "Relating to guardianships and conservatorships.\n"
    "Section 1. A court shall remove a proceeding.\n"
    "Section 2. This act is effective on October 1,\n"
    "2026.\n"
)


def _clean_pair(prior, raw):
    return apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Alabama",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


def test_the_synopsis_block_is_removed_from_an_introduced_text():
    cleaned = _clean_al_text(INTRODUCED)
    assert "SYNOPSIS" not in cleaned and "existing law" not in cleaned
    assert "Section 1. A court may remove a proceeding." in cleaned


def test_introduced_to_engrossed_leaves_only_the_real_change():
    prior, raw = _clean_pair(INTRODUCED, ENGROSSED)
    # compared as lines, the way difflib sees them (a trailing newline is not a line)
    assert prior.replace("may remove", "shall remove").splitlines() == raw.splitlines()


def test_engrossed_to_enrolled_leaves_nothing_when_only_the_wrapper_differs():
    prior, raw = _clean_pair(ENGROSSED, ENROLLED)
    assert prior.splitlines() == raw.splitlines()


def test_the_history_block_is_cut_with_its_chamber_line():
    cleaned = _clean_al_text(ENGROSSED)
    assert cleaned.endswith("2026.")
    for gone in ("House of Representatives", "Read for the first", "Yeas", "Clerk"):
        assert gone not in cleaned


def test_the_tracking_code_is_dropped_only_from_line_two():
    assert _clean_al_text("HB1\nABC12-3\nbody\nXYZ99-9").splitlines() == [
        "HB1",
        "body",
        "XYZ99-9",
    ]
    # a second line that is not a tracking code is kept
    assert _clean_al_text("HB1\nBy Representative Smith\nbody") == (
        "HB1\nBy Representative Smith\nbody"
    )


@pytest.mark.parametrize(
    "line",
    [
        # Real text that merely resembles the title lines: only a WHOLE line is dropped.
        "AN ACT TO AMEND SECTION 5",
        "A BILL TO BE ENTITLED",
        "Enrolled, An Act, relating to schools",
        "to be entitled",
    ],
)
def test_real_text_that_resembles_the_title_block_is_kept(line):
    assert _clean_al_text(f"HB1\nTRK12-1\n{line}\nbody") == f"HB1\n{line}\nbody"


@pytest.mark.parametrize(
    "text",
    [
        # A SYNOPSIS: with no "A BILL" after it is not cut: nothing marks where it ends.
        "HB1\nTRK12-1\nSYNOPSIS:\nsummary text\nSection 1. Real text.",
        # The history marker only counts at the START of a line.
        "Section 1. The court Read for the first time and referred the matter.\nSection 2.",
        "The Senate\nSection 2. More text.",
    ],
)
def test_no_anchor_means_nothing_is_cut(text):
    cleaned = _clean_al_text(text)
    for fragment in ("summary text", "Read for the first time", "More text"):
        if fragment in text:
            assert fragment in cleaned


def test_a_real_change_survives_the_cleaner():
    old = "HB1\nTRK12-1\nA BILL\nSection 1. A court may act.\n"
    new = "HB1\nTRK12-2\nA BILL\nSection 1. A court shall act.\n"
    cleaned_old, cleaned_new = _clean_pair(old, new)
    assert "may" in cleaned_old and "shall" in cleaned_new
    assert cleaned_old != cleaned_new


@pytest.mark.parametrize(
    "jurisdiction", ["Florida", "Massachusetts", "United States", "Utah"]
)
def test_the_cleaner_is_alabama_only(jurisdiction):
    text = "HB1\nTRK12-1\nSYNOPSIS:\nx\nA BILL\nbody\n"
    assert apply_prediff_cleaning(
        text,
        text,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    ) == (text, text)


@pytest.mark.django_db
def test_archiving_introduced_engrossed_enrolled_keeps_only_real_changes():
    urls = {
        "Introduced": "https://x.test/al/i.pdf",
        "Engrossed": "https://x.test/al/e.pdf",
        "Enrolled": "https://x.test/al/r.pdf",
    }
    texts = {
        urls["Introduced"]: INTRODUCED,
        urls["Engrossed"]: ENGROSSED,
        urls["Enrolled"]: ENROLLED,
    }
    bill = _make_bill(jid=AL, jurisdiction_name="Alabama")
    bill.classification = ["bill"]
    bill.save()
    for note in ("Enrolled", "Introduced", "Engrossed"):  # out of row order on purpose
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(bill, texts)
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}

    changed = [
        line
        for line in docs["Engrossed"].diff_from_previous_version.splitlines()
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]
    assert changed == [
        "-Section 1. A court may remove a proceeding.",
        "+Section 1. A court shall remove a proceeding.",
    ]
    assert docs["Enrolled"].diff_from_previous_version == ""
    assert docs["Engrossed"].raw_text == ENGROSSED  # stored text untouched


@pytest.mark.django_db
def test_the_recompute_path_agrees_with_the_archiver_for_alabama():
    urls = {
        "Introduced": "https://x.test/al/i.pdf",
        "Engrossed": "https://x.test/al/e.pdf",
    }
    bill = _make_bill(jid=AL, jurisdiction_name="Alabama")
    bill.classification = ["bill"]
    bill.save()
    for note in ("Introduced", "Engrossed"):
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(bill, {urls["Introduced"]: INTRODUCED, urls["Engrossed"]: ENGROSSED})
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Alabama", is_bill=True, bill_identifier="HB 1"
    )
    assert result["changed"] == []


def test_a_history_marker_without_the_chamber_line_before_it_cuts_nothing():
    """The review's high-severity case: bill text that starts "Read for the first time" must not
    take the rest of the document with it unless the exact chamber line precedes it."""
    text = (
        "HB1\nTRK12-1\nA BILL\n"
        "Section 1. A court may act.\n"
        "Read for the first time in any proceeding, the notice shall state the date.\n"
        "Section 2. More real text.\n"
        "Section 3. Even more real text.\n"
    )
    assert "More real text" in _clean_al_text(text)
    assert "Read for the first time in any proceeding" in _clean_al_text(text)


def test_a_chamber_line_and_marker_far_from_the_end_cuts_nothing():
    body = "".join(f"Section {n}. Real text number {n}.\n" for n in range(40))
    text = f"HB1\nTRK12-1\nA BILL\nSenate\nRead for the first time and referred\n{body}"
    cleaned = _clean_al_text(text)
    assert "Real text number 39" in cleaned and "Read for the first time" in cleaned


def test_an_exact_title_phrase_later_in_the_document_is_kept():
    body = "".join(f"Section {n}. Real text number {n}.\n" for n in range(40))
    text = f"HB1\nTRK12-1\nA BILL\n{body}AN ACT\nSection 41. After the title region."
    assert "\nAN ACT\n" in _clean_al_text(text)


@pytest.mark.parametrize("second_line", ["jpkgjnn-1", "AB-1", "ABCD", "By Rep. Smith"])
def test_only_an_uppercase_tracking_code_on_line_two_is_dropped(second_line):
    assert second_line in _clean_al_text(f"HB1\n{second_line}\nbody")
