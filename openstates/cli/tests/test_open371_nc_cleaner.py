"""OPEN-371: North Carolina's pre-diff cleaner.

The "Ratified" printing and the "SL <year>-<N>" session law are typeset differently from the
"Edition N" printings (indented lines, a header block, running page lines, a signature block), so
a raw diff between them was 5 to 10 times larger than the real change. The fixtures below have
the shapes measured on the production replica (HB26: Edition 3 is flush left and starts "A BILL TO
BE ENTITLED"; its Ratified printing indents every line and adds the header and footers).
"""

import pytest

from openstates.cli.text_extract import (
    _clean_nc_text,
    apply_prediff_cleaning,
    archive_bill_versions,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

NC = "ocd-jurisdiction/country:us/state:nc/government"

EDITION = (
    "A BILL TO BE ENTITLED\n"
    "AN ACT TO DEANNEX CERTAIN DESCRIBED TERRITORIES AND MAKE OTHER\n"
    "CHANGES TO VARIOUS LOCAL LAWS.\n"
    "The General Assembly of North Carolina enacts:\n"
    "SECTION 1. G.S. 160A-58.1(b) reads as rewritten:\n"
    '"(b) A noncontiguous area proposed for annexation must meet all of the following\n'
    "standards:\n"
    "…\n"
    "SECTION 9. Except as otherwise provided, this act is effective when it becomes\n"
    "law.\n"
)

RATIFIED = (
    "                    GENERAL ASSEMBLY OF NORTH CAROLINA\n"
    "                                SESSION 2025\n"
    "\n"
    "                                      HOUSE BILL 26\n"
    "                                      RATIFIED BILL\n"
    "\n"
    "\n"
    "AN ACT TO DEANNEX CERTAIN DESCRIBED TERRITORIES AND MAKE OTHER\n"
    "  CHANGES TO VARIOUS LOCAL LAWS.\n"
    "\n"
    "The General Assembly of North Carolina enacts:\n"
    "           SECTION 1. G.S. 160A-58.1(b) reads as rewritten:\n"
    '    "(b) A noncontiguous area proposed for annexation must meet all of the following\n'
    "standards:\n"
    "           …\n"
    "                                  *H26-v-4*\n"
    "Page 2 House Bill 26-Ratified\n"
    "           SECTION 9. Except as otherwise provided, this act is effective when it becomes\n"
    "law.\n"
    "\n"
    "      In the General Assembly read three times and ratified this the 23rd day of September,\n"
    "2025.\n"
    "                  s/ Destin Hall\n"
    "                     Speaker of the House of Representatives\n"
    "                  _____________________________________\n"
    "                     Josh Stein\n"
    "                     Governor\n"
    "Approved __________.m. this ______________ day of ___________________, 2025\n"
    "House Bill 26-Ratified Page 3\n"
    "Page 8                                                                \n"
    "\x0c\n"
)


def test_a_ratified_printing_cleans_to_the_same_text_as_its_edition():
    assert _clean_nc_text(RATIFIED) == _clean_nc_text(EDITION).replace(
        "A BILL TO BE ENTITLED\n", ""
    )


def test_the_edition_and_ratified_diff_is_empty_once_cleaned():
    cleaned_edition, cleaned_ratified = apply_prediff_cleaning(
        EDITION,
        RATIFIED,
        jurisdiction_name="North Carolina",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert cleaned_edition == cleaned_ratified


@pytest.mark.parametrize(
    "line",
    [
        "A BILL TO BE ENTITLED",
        "GENERAL ASSEMBLY OF NORTH CAROLINA",
        "SESSION 2025",
        "HOUSE BILL 171",
        "SENATE BILL 50",
        "RATIFIED BILL",
        "SESSION LAW 2025-14",
        "*H26-v-4*",
        "*S50-v-12*",
        "Page 2 House Bill 26-Ratified",
        "Senate Bill 50-Ratified Page 3",
        "Page 4 Session Law 2025-14 House Bill 26",
        "House Bill 26 Session Law 2025-14 Page 5",
    ],
)
def test_the_measured_header_and_page_lines_are_dropped(line):
    assert _clean_nc_text(f"before\n{line}\nafter") == "before\nafter"


@pytest.mark.parametrize(
    "line",
    [
        # Real bill text that merely resembles the noise: a line is dropped only if the WHOLE line
        # is one of the measured shapes.
        "GENERAL ASSEMBLY OPERATING EXPENSES",
        "GENERAL ASSEMBLY OPERATING FUNDS AND GENERAL ASSEMBLY SPECIAL",
        "See Page 3 of the report.",
        "Page 8",  # a bare page number matched no real document, so it is not treated as noise
        "The session 2025 appropriations are ratified by this act.",
        "HOUSE BILL 171 is amended as follows:",
        "SESSION LAW 2025-14 is repealed.",
        "…",
    ],
)
def test_real_text_that_resembles_the_noise_is_kept(line):
    assert _clean_nc_text(f"before\n{line}\nafter") == f"before\n{line}\nafter"


def test_the_signature_block_is_cut_at_the_ratification_marker_and_nothing_before_it():
    text = "SECTION 1. Real text.\nIn the General Assembly read three times and ratified this the 9th day\ns/ A Person\nGovernor\nSECTION 99. After."
    assert _clean_nc_text(text) == "SECTION 1. Real text."


def test_whitespace_is_collapsed_and_blank_lines_are_dropped():
    assert _clean_nc_text("  A   b\t c  \n\n\n   d\n") == "A b c\nd"


def test_a_real_change_between_editions_survives_the_cleaner():
    old = "A BILL TO BE ENTITLED\nSECTION 1. A court may consider the award.\n"
    new = "A BILL TO BE ENTITLED\nSECTION 1. A court shall consider the award.\n"
    cleaned_old, cleaned_new = apply_prediff_cleaning(
        old,
        new,
        jurisdiction_name="North Carolina",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert cleaned_old != cleaned_new
    assert "may" in cleaned_old and "shall" in cleaned_new


@pytest.mark.parametrize(
    "jurisdiction", ["Alabama", "Florida", "Massachusetts", "United States", "Utah"]
)
def test_the_cleaner_is_north_carolina_only(jurisdiction):
    """Another state's text with NC-shaped lines is not touched by this cleaner (asserted for
    states that have no cleaner of their own)."""
    prior, raw = "Page 8\nRATIFIED BILL\nx", "Page 9\nRATIFIED BILL\ny"
    cleaned = apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert "RATIFIED BILL" in cleaned[0] and "RATIFIED BILL" in cleaned[1]


@pytest.mark.django_db
def test_archiving_an_edition_and_its_ratified_printing_gives_an_empty_diff():
    """End to end through the archiver: Edition 3 -> Ratified of the same text is a typesetting
    change only, so the stored diff is "" (identical text), which the changelog path skips."""
    urls = {
        "Edition 3": "https://x.test/nc/v3.pdf",
        "Ratified": "https://x.test/nc/v4.pdf",
    }
    bill = _make_bill(jid=NC, jurisdiction_name="North Carolina")
    bill.classification = ["bill"]
    bill.save()
    for note in ("Ratified", "Edition 3"):  # out of row order on purpose
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(bill, {urls["Edition 3"]: EDITION, urls["Ratified"]: RATIFIED})
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert docs["Edition 3"].diff_from_previous_version is None
    assert docs["Ratified"].diff_from_previous_version == ""
    # the stored text is untouched; only the diff input is cleaned
    assert docs["Ratified"].raw_text == RATIFIED


@pytest.mark.django_db
def test_the_recompute_path_agrees_with_the_archiver_for_north_carolina():
    urls = {
        "Edition 3": "https://x.test/nc/v3.pdf",
        "Ratified": "https://x.test/nc/v4.pdf",
    }
    bill = _make_bill(jid=NC, jurisdiction_name="North Carolina")
    bill.classification = ["bill"]
    bill.save()
    for note in ("Edition 3", "Ratified"):
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(bill, {urls["Edition 3"]: EDITION, urls["Ratified"]: RATIFIED})
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="North Carolina", is_bill=True
    )
    assert result["changed"] == []


@pytest.mark.parametrize(
    "near_miss",
    [
        # The marker is a whole sentence at the START of a line. Anything else must keep every
        # line that follows.
        "SECTION 3. In the General Assembly read three times and ratified this the 9th day",
        "The General Assembly read three times and ratified this the 9th day",
        "in the general assembly read three and ratified",
        "Whereas the General Assembly read three times and ratified",
    ],
)
def test_a_line_that_only_resembles_the_ratification_marker_does_not_truncate(
    near_miss,
):
    text = f"SECTION 1. Real text.\n{near_miss}\nSECTION 2. More real text."
    assert _clean_nc_text(text) == text
