"""OPEN-499: Pennsylvania's second change bar, the arrow glyph "◄".

Some Pennsylvania printings draw the right-margin change bar as "◄" (U+25C4) instead of "<--".
OPEN-495's cleaner removed only "<--", so the glyph stayed in 12 stored comparisons. The fixtures
are real lines from the production replica (SB 720 PN1806, HB 575 PN0959, HB 2649 PN3663/PN3753,
Pennsylvania 2025-2026).
"""

import types

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

# HB 2649: the same four lines in PN3663 and PN3753; the later printing marks two with the glyph.
HB2649_PN3663 = (
    "section:\n"
    "(1)     Coverage under this section shall include storage for\n"
    "not less than three years at an in-network facility.\n"
    "(2)     If an insurer does not have an in-network facility\n"
)
HB2649_PN3753 = (
    "section:                                                            ◄\n"
    "(1)     Coverage under this section shall include storage for    ◄\n"
    "not less than three years at an in-network facility.\n"
    "(2)     If an insurer does not have an in-network facility\n"
)


@pytest.mark.parametrize(
    "line, cleaned",
    [
        (
            "LAW 88-352, 78 STAT. 241).                                          ◄",
            "law 88-352, 78 stat. 241).",
        ),
        (
            "Banking, Department of Health, Department of Agriculture or the    ◄",
            "banking, department of health, department of agriculture or the",
        ),
        # no space before the glyph (HB 2649 PN3753)
        (
            '"Fertility preservation service" means a procedure or service ◄',
            '"fertility preservation service" means a procedure or service',
        ),
    ],
)
def test_the_arrow_change_bar_is_removed_from_the_end_of_a_real_line(line, cleaned):
    assert _clean_pa_text(line) == cleaned


def test_an_arrow_in_the_middle_of_a_line_is_kept():
    # every glyph measured on the replica (449 lines) ends its line; one inside a line is text
    assert _clean_pa_text("see the ◄ symbol in the margin") == (
        "see the ◄ symbol in the margin"
    )


def test_only_the_last_mark_on_a_line_is_removed():
    assert _clean_pa_text("a ◄ b   ◄") == "a ◄ b"


def test_the_ascii_change_bar_is_still_removed():
    assert _clean_pa_text("Section 1.  The act is amended.   <--") == (
        "section 1. the act is amended."
    )


def test_a_real_change_on_an_arrow_marked_line_survives():
    # SB 720 PN1806: the earlier printing had "241 ????)." (an unmapped character); the change
    # bar goes, the real difference stays.
    prior, raw = apply_prediff_cleaning(
        "law 88-352, 78 stat. 241 ????).",
        "LAW 88-352, 78 STAT. 241).                                          ◄",
        jurisdiction_name="Pennsylvania",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert prior == "law 88-352, 78 stat. 241 ????)."
    assert raw == "law 88-352, 78 stat. 241)."


@pytest.mark.parametrize("jurisdiction", ["Georgia", "Louisiana", "Utah"])
def test_the_arrow_is_left_alone_outside_pennsylvania(jurisdiction):
    raw = "ONE   ◄"
    assert apply_prediff_cleaning(
        raw,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    ) == (raw, raw)


def _doc(doc_id, note, text, stored_diff=None):
    return types.SimpleNamespace(
        id=doc_id,
        version_note=note,
        version_date="",
        source_url=f"https://x.test/pa/{doc_id}.pdf",
        media_type="application/pdf",
        raw_text=text,
        is_error=False,
        diff_from_previous_version=stored_diff,
    )


def test_recompute_drops_a_comparison_that_was_only_arrow_change_bars():
    stored = "--- \n+++ \n@@ -1,2 +1,2 @@\n-section:\n+section: ◄\n"
    docs = [
        _doc(1, "Printer's No. PN3663", HB2649_PN3663),
        _doc(2, "Printer's No. PN3753", HB2649_PN3753, stored),
    ]
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Pennsylvania", is_bill=True, bill_identifier="HB 2649"
    )
    changed = {doc.id: diff for doc, diff in result["changed"]}
    assert changed == {2: ""}


@pytest.mark.django_db
def test_archiving_a_pennsylvania_bill_leaves_no_arrow_in_the_diff():
    bill = _make_bill(jid=PA, jurisdiction_name="Pennsylvania")
    bill.classification = ["bill"]
    bill.save()
    urls = {}
    for note in ("Printer's No. PN3663", "Printer's No. PN3753"):
        urls[note] = f"https://x.test/pa/{note.replace(' ', '_')}.pdf"
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(
        bill,
        {
            urls["Printer's No. PN3663"]: HB2649_PN3663,
            urls["Printer's No. PN3753"]: HB2649_PN3753,
        },
    )
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert docs["Printer's No. PN3753"].diff_from_previous_version == ""
    # only the diff input is cleaned: the stored text keeps the glyph
    assert "◄" in docs["Printer's No. PN3753"].raw_text
