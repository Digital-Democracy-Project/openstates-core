"""OPEN-486: Nebraska's pre-diff cleaner and the exclusion of its amendment papers.

The "Slip Law" printing is typeset differently from "Introduced" and "Final Reading" (running
header, title, sponsor line, page numbers, a different line width), so a raw diff reported the
whole document as changed (360 of 365 Final Reading -> Slip Law pairs). The fixtures have the
shapes read from the production replica (LB 1237: the first lines of its three printings).
"""

import pytest

from openstates.cli.text_extract import (
    _clean_ne_text,
    apply_prediff_cleaning,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

NE = "ocd-jurisdiction/country:us/state:ne/government"

FINAL_READING = (
    "2026\n"
    "A   BILL   FOR   AN   ACT   relating   to   the   State   Capitol;   to   amend   section\n"
    "28-1202.01,      Revised   Statutes     Cumulative     Supplement,     2024,    and\n"
    "sections 28-101 and 81-1108.15, Revised Statutes Supplement, 2025;\n"
    "to prohibit certain weapons from being brought into the State Capitol.\n"
    "Be it enacted by the people of the State of Nebraska,\n"
    "2026\n"
    "Section 1. Section 28-101, Revised Statutes Supplement, 2025, is\n"
    "amended to read:\n"
    "28-101 Sections 28-101 to 28-1357 shall be known and may be cited as the Nebraska\n"
    "Criminal Code.\n"
)

SLIP_LAW = (
    "LB1237                                                                   LB1237\n"
    "2026                                                                       2026\n"
    "\n"
    "\n"
    "                    LEGISLATIVE BILL 1237\n"
    "         Became law April 17, 2026, without approval of the Governor.\n"
    "\n"
    "\n"
    "Introduced by Executive Board: Hansen, 16, Chairperson; Arch, 14; Ballard, 21;\n"
    "\n"
    "A BILL FOR AN ACT relating to the State Capitol; to amend section 28-1202.01,\n"
    "     Revised Statutes Cumulative Supplement, 2024, and sections 28-101 and\n"
    "     81-1108.15, Revised Statutes Supplement, 2025; to prohibit certain weapons\n"
    "     from being brought into the State Capitol.\n"
    "Be it enacted by the people of the State of Nebraska,\n"
    "Section 1. Section 28-101, Revised Statutes Supplement, 2025, is\n"
    "     amended to read:\n"
    "     28-101 Sections 28-101 to 28-1357 shall be known and may be cited as the\n"
    "     Nebraska Criminal Code.\n"
    "\n"
    "                                      -2-\n"
    "\x0c"
)


def _clean(prior, raw):
    return apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Nebraska",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


def test_a_slip_law_cleans_to_the_same_text_as_its_final_reading():
    assert _clean_ne_text(SLIP_LAW) == _clean_ne_text(FINAL_READING)


def test_final_reading_and_slip_law_diff_is_empty_once_cleaned():
    cleaned_final, cleaned_slip = _clean(FINAL_READING, SLIP_LAW)
    assert cleaned_final == cleaned_slip


def test_the_cleaned_text_has_one_sentence_per_line():
    assert _clean_ne_text("One is here.   Two is here;\n  three is here: four.\n") == (
        "One is here.\nTwo is here;\nthree is here:\nfour."
    )


@pytest.mark.parametrize(
    "line",
    [
        "LB1237                    LB1237",
        "LB1237 LB1237",
        "LB380A  LB380A",
        "2026",
        "2026                       2026",
        "LEGISLATIVE BILL 1237",
        "LEGISLATIVE BILL 380A",
        "          Became law April 17, 2026, without approval of the Governor.",
        "Approved by the Governor April 17, 2026",
        "Introduced by Executive Board: Hansen, 16, Chairperson;",
        "-4-",
    ],
)
def test_the_measured_header_lines_are_dropped(line):
    assert _clean_ne_text(f"Before the line.\n{line}\nAfter the line.") == (
        "Before the line.\nAfter the line."
    )


@pytest.mark.parametrize(
    "line",
    [
        # Real bill text that merely resembles a header: a line is dropped only if the WHOLE line
        # is one of the measured shapes.
        "2026,",
        "in 2026",
        "2026 and 2027",
        "LEGISLATIVE BILL 1237 is amended.",
        "Section 5 of LEGISLATIVE BILL 1237",
        "This act became law in 2026.",
        "Not Introduced by anyone",
        "-4- of the statute",
        "LB1237",
        # two different identifiers are not a running header (the two halves must be identical)
        "LB123 LB456",
        "LB123 AM456",
        "LB123A LB123",
    ],
)
def test_real_text_that_resembles_a_header_is_kept(line):
    assert line.strip() in _clean_ne_text(f"Before the line.\n{line}\nAfter the line.")


def test_a_real_change_between_printings_survives_the_cleaner():
    old = "Section 1. A court may consider the award.\nSec. 2. Done.\n"
    new = "Section 1. A court shall consider the award.\nSec. 2. Done.\n"
    cleaned_old, cleaned_new = _clean(old, new)
    assert cleaned_old != cleaned_new
    assert "may" in cleaned_old and "shall" in cleaned_new


def test_a_sentence_edit_does_not_shift_the_lines_after_it():
    """The point of one sentence per line: a longer first sentence re-wraps every later line of a
    fixed-width printing, but only its own cleaned line changes."""
    old = "First sentence is here. Second one stays. Third one stays too."
    new = "First sentence is here and grows by quite a few more words. Second one stays. Third one stays too."
    cleaned_old, cleaned_new = _clean(old, new)
    assert cleaned_old.split("\n")[1:] == cleaned_new.split("\n")[1:]
    assert cleaned_old.split("\n")[0] != cleaned_new.split("\n")[0]


@pytest.mark.parametrize(
    "jurisdiction", ["Alabama", "Florida", "Louisiana", "Utah"]
)
def test_the_cleaner_is_nebraska_only(jurisdiction):
    prior, raw = "LB1237      LB1237\n2026\nx. y.", "LB1237      LB1237\n2026\nz. w."
    cleaned = apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert cleaned == (prior, raw)


def _ne_bill():
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")
    bill.classification = ["bill"]
    bill.save()
    urls = {}
    for note in ("Slip Law", "Final Reading", "Introduced"):
        urls[note] = f"https://x.test/ne/{note.replace(' ', '_')}.pdf"
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    return bill, urls


def _texts(urls):
    return {
        urls["Introduced"]: FINAL_READING.replace(
            "shall be known", "shall be known, as before,"
        ),
        urls["Final Reading"]: FINAL_READING,
        urls["Slip Law"]: SLIP_LAW,
    }


@pytest.mark.django_db
def test_archiving_a_nebraska_bill_cleans_the_slip_law_diff():
    bill, urls = _ne_bill()
    _archive(bill, _texts(urls))
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert docs["Introduced"].diff_from_previous_version is None
    assert docs["Final Reading"].diff_from_previous_version  # a real change, shown
    # Slip Law diffs against Final Reading: only typesetting differs, so once cleaned there is
    # nothing to show.
    assert docs["Slip Law"].diff_from_previous_version == ""
    # only the diff input is cleaned: the stored text is untouched
    assert docs["Slip Law"].raw_text == SLIP_LAW


@pytest.mark.django_db
def test_the_recompute_path_agrees_with_the_archiver_for_nebraska():
    bill, urls = _ne_bill()
    _archive(bill, _texts(urls))
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Nebraska", is_bill=True, bill_identifier="LB 1"
    )
    assert result["changed"] == []
