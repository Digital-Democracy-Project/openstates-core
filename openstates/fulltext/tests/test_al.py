"""OPEN-530: Alabama's PDFs, read with struck-out words left out.

The fixtures are real pages of SB 292 (2026RS) from alison.legislature.state.al.us, each cut
from the published PDF with PyMuPDF (pdftotext reads the cut page exactly as it reads that page
in the whole file): page 25 of the Introduced printing and page 24 of the Engrossed printing,
both holding section 13A-9-22, where "A misdemeanor" is struck and "D felony" is underlined, and
page 1 of the Engrossed printing, which has nothing struck or underlined.
"""

from pathlib import Path

from openstates.cli.text_extract import _clean_al_text
from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.al import handle_alabama_pdf, without_struck_text
from openstates.fulltext.common import extract_line_numbered_pdf

TESTDATA = Path(__file__).parent / "testdata"
AL = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:al/government"}


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _lines(text: str) -> list:
    return [" ".join(line.split()) for line in text.split("\n")]


def test_section_13a_9_22_reads_class_d_felony_in_both_versions():
    for name in ("al_sb292_introduced_p25.pdf", "al_sb292_engrossed_p24.pdf"):
        lines = _lines(handle_alabama_pdf(_read(name), AL))
        assert "property is a Class D felony." in lines, name
        text = "\n".join(lines)
        assert "misdemeanor D felony" not in text
        assert "Class A misdemeanor" not in text


def test_before_this_change_the_struck_words_were_read_beside_the_new_ones():
    # The same page read the old way: the defect the ticket reports.
    lines = _lines(extract_line_numbered_pdf(_read("al_sb292_engrossed_p24.pdf"), AL))
    assert "property is a Class A misdemeanor D felony." in lines


def test_only_struck_words_are_removed():
    # Each line read now is its line read the old way with characters taken out, never added
    # (spacing aside), and only the struck ones: here "A misdemeanor" and the struck "or" of
    # "Lists or, advertises" and "Rents or, leases".
    data = _read("al_sb292_engrossed_p24.pdf")
    old = [ln for ln in _lines(extract_line_numbered_pdf(data, AL)) if ln]
    new = [ln for ln in _lines(handle_alabama_pdf(data, AL)) if ln]
    assert len(new) == len(old)
    removed = []
    for before, after in zip(old, new):
        rest = iter(before.replace(" ", ""))
        assert all(ch in rest for ch in after.replace(" ", "")), (before, after)
        if before != after:
            removed.append((before, after))
    assert removed == [
        (
            "(1) Lists or, advertises, or causes to list or",
            "(1) Lists , advertises, or causes to list or",
        ),
        (
            "(2) Rents or, leases, or causes to rent or lease",
            "(2) Rents , leases, or causes to rent or lease",
        ),
        (
            "property is a Class A misdemeanor D felony.",
            "property is a Class D felony.",
        ),
    ]


def test_a_page_with_nothing_struck_is_read_exactly_as_before():
    data = _read("al_sb292_engrossed_p1.pdf")
    assert without_struck_text(data) is data
    assert handle_alabama_pdf(data, AL) == extract_line_numbered_pdf(data, AL)


def test_the_comparison_sees_no_change_on_the_unchanged_line():
    # Introduced -> Engrossed did not touch section 13A-9-22; after Alabama's pre-diff cleaner
    # both read the same line, so the comparison has nothing there to describe.
    introduced = _clean_al_text(
        handle_alabama_pdf(_read("al_sb292_introduced_p25.pdf"), AL)
    )
    engrossed = _clean_al_text(
        handle_alabama_pdf(_read("al_sb292_engrossed_p24.pdf"), AL)
    )
    assert "property is a Class D felony." in introduced.split("\n")
    assert "property is a Class D felony." in engrossed.split("\n")


def test_only_alabama_uses_the_alabama_reader():
    users = [
        state
        for state, funcs in CONVERSION_FUNCTIONS.items()
        for func in funcs.values()
        if func is handle_alabama_pdf
    ]
    assert users == ["al"]
    assert CONVERSION_FUNCTIONS["al"] == {"application/pdf": handle_alabama_pdf}
