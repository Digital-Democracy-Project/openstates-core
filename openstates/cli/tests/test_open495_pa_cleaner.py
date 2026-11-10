"""OPEN-495: Pennsylvania's pre-diff cleaner.

Pennsylvania prints a change bar ("<--") at the end of each new or reprinted line, prints new
language in CAPITALS and folds it back to normal case in the next printing, spaces the same line
differently from one printing to the next, and opens some documents with a "PRINTER'S NO. 3745"
line. A raw diff reported all of that as changes. The fixtures are real lines from the production
replica (HB 1667, Pennsylvania 2025-2026).
"""

import pytest

from openstates.cli.text_extract import (
    _clean_pa_text,
    apply_prediff_cleaning,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

PA = "ocd-jurisdiction/country:us/state:pa/government"

# The first printing: new language is in CAPITALS and every new line carries the change bar.
FIRST_PRINTING = (
    "Section 2.        Sections 1828-G(c) and 1829-G(b)(3) of the act\n"
    "are amended to read:\n"
    "SECTION 1.        SECTION 1828-G(C) OF THE ACT OF MARCH 4, 1971      <--\n"
    "(P.L.6, NO.2), KNOWN AS THE TAX REFORM CODE OF 1971, IS AMENDED\n"
    "TO READ:\n"
    "Section 1828-G.        Business firms.\n"
    "(c)   Limitation.--The department may not approve more than\n"
    "[$30,000,000] $60,000,000 in credit-eligible capital\n"
)

# The next printing: the capitals are folded back to normal case and the spacing differs.
REPRINT = (
    "PRINTER'S NO.   3745\n"
    "Section 2.     Sections 1828-G(c) and 1829-G(b)(3) of the act\n"
    "are amended to read:\n"
    "Section 1.     Section 1828-G(c) of the act of March 4, 1971\n"
    "(P.L.6, No.2), known as the Tax Reform Code of 1971, is amended\n"
    "to read:\n"
    "Section 1828-G.    Business firms.\n"
    "(c)     Limitation.--The department may not approve more than\n"
    "[$30,000,000] $60,000,000 in credit-eligible capital\n"
)


def _clean(prior, raw):
    return apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Pennsylvania",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


def test_a_reprint_that_only_folds_case_and_spacing_cleans_to_the_same_text():
    cleaned_first, cleaned_reprint = _clean(FIRST_PRINTING, REPRINT)
    assert cleaned_first == cleaned_reprint


def test_the_change_bar_is_removed_from_the_end_of_a_line():
    assert _clean_pa_text("Section 1.  The act is amended.   <--") == (
        "section 1. the act is amended."
    )


def test_a_change_bar_in_the_middle_of_a_line_is_kept():
    # only the right-margin marker is a change bar; anything else is real text
    assert "<--" in _clean_pa_text("a <-- b")


@pytest.mark.parametrize(
    "line", ["PRINTER'S NO.   3745", "PRINTER'S NO. 1030", "printer's no.  2676"]
)
def test_the_printers_number_line_is_dropped(line):
    assert _clean_pa_text(f"{line}\nAmending the act") == "amending the act"


@pytest.mark.parametrize(
    "line",
    [
        # Real text that merely mentions a printer: only the whole header line goes.
        "paper clips, printer ink, calculators, compasses, protractors,",
        "present shall decide to revert to a prior printer's number. If",
        "the printer's no. 3745 is reprinted",
        "PRINTER'S NO.",
    ],
)
def test_real_text_that_mentions_a_printer_is_kept(line):
    assert _clean_pa_text(f"Before.\n{line}\nAfter.").split("\n")[1] == line.lower()


def test_a_real_change_between_printings_survives_the_cleaner():
    old = "(3)   The application shall be filed no later than February 1.\n"
    new = "(3)   THE APPLICATION SHALL BE FILED NO LATER THAN MARCH 1.   <--\n"
    cleaned_old, cleaned_new = _clean(old, new)
    assert cleaned_old != cleaned_new
    assert "february 1" in cleaned_old and "march 1" in cleaned_new


def test_bracketed_words_are_real_text_and_are_kept():
    assert "[either] the earliest" in _clean_pa_text("(2)   [either] the earliest of")


def test_the_stored_text_is_not_touched_by_the_cleaner():
    original = "ONE   <--"
    _clean_pa_text(original)
    assert original == "ONE   <--"


@pytest.mark.parametrize(
    "jurisdiction",
    ["Georgia", "Florida", "Louisiana", "Ohio", "Utah"],
)
def test_the_cleaner_is_pennsylvania_only(jurisdiction):
    prior, raw = "ONE   <--\nTwo", "ONE   <--\nThree"
    cleaned = apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert cleaned == (prior, raw)


def _pa_bill():
    bill = _make_bill(jid=PA, jurisdiction_name="Pennsylvania")
    bill.classification = ["bill"]
    bill.save()
    urls = {}
    for note in ("Printer's No. PN3708", "Printer's No. PN3745"):
        urls[note] = f"https://x.test/pa/{note.replace(' ', '_')}.pdf"
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    return bill, urls


@pytest.mark.django_db
def test_archiving_a_pennsylvania_bill_leaves_no_change_bar_or_case_noise_in_the_diff():
    bill, urls = _pa_bill()
    _archive(
        bill,
        {
            urls["Printer's No. PN3708"]: FIRST_PRINTING,
            urls["Printer's No. PN3745"]: REPRINT,
        },
    )
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    diff = docs["Printer's No. PN3745"].diff_from_previous_version
    # only case, spacing, the change bar and the printer's number differ, so nothing is shown
    assert diff == ""
    assert "<--" not in diff
    # only the diff input is cleaned: the stored text is untouched
    assert docs["Printer's No. PN3745"].raw_text == REPRINT
    assert docs["Printer's No. PN3708"].raw_text == FIRST_PRINTING


@pytest.mark.django_db
def test_the_recompute_path_agrees_with_the_archiver_for_pennsylvania():
    bill, urls = _pa_bill()
    _archive(
        bill,
        {
            urls["Printer's No. PN3708"]: FIRST_PRINTING,
            urls["Printer's No. PN3745"]: REPRINT,
        },
    )
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Pennsylvania", is_bill=True, bill_identifier="HB 1667"
    )
    assert result["changed"] == []
