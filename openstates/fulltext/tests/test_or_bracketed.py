"""OPEN-600: Oregon PDFs, read without the words a bill deletes from current law, which Oregon
prints [italic and bracketed] (fulltext/oregon.py).

The fixtures are real pages of 2026R1 PDFs, each cut from the published file with PyMuPDF:

* HB 4010, Introduced, page 1: ORS 279C.570 amended ("Payments [shall] must be based", "due the
  contractor[. The interest shall commence] beginning 30 days after [receipt of the] receiving an
  invoice"), with its margin line numbers and the bill's header.
* HB 4139, Introduced, page 2: whole subsections deleted, one bracketed line after another.
* HB 4139, Introduced, page 3: renumbered subsections ("[(20)] (21)") and the chemical name
  "6H-benzo[c]chromen-1-ol", whose brackets print upright.
* HB 4004, House amendment 11 (HALNRW), page 2: an amendment paper quoting a bracketed deletion
  that runs over a line break.
* SB 1563, Senate amendment 2 (SJUD), page 1: an amendment paper quoting a court with an upright
  "[,]".
"""

import re
from pathlib import Path

import pytest

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.common import extract_sometimes_numbered_pdf
from openstates.fulltext.oregon import drop_bracketed, upright_brackets
from openstates.fulltext.utils import pdfdata_to_text

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:or/government"}
READ = CONVERSION_FUNCTIONS["or"]["application/pdf"]


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _words(text: str) -> str:
    return " ".join(text.split())


def _is_subsequence(new: str, old: str) -> bool:
    rest = iter(old)
    return all(ch in rest for ch in new)


def _outside_brackets(text: str) -> str:
    return re.sub(r"\s", "", re.sub(r"\[[^\[\]]*\]", "", text))


def test_hb4010_reads_the_new_words_only():
    data = _read("or_hb4010_introduced_p1.pdf")
    old = extract_sometimes_numbered_pdf(data, META)
    new = READ(data, META)
    assert "Payments [shall] must be based" in old  # the defect the ticket reports
    words = _words(new)
    assert "Payments must be based upon estimates" in words
    assert "A progress payment is not acceptance or approval of any work" in words
    assert "waiver of any defects in the work" in words
    assert "due the contractor beginning 30 days after receiving an invoice" in words
    assert "15 days after the contracting agency approves the payment," in words
    assert "[" not in new and "]" not in new
    lines = new.split("\n")
    # Spacing is tidied: no doubled space where a word went, none at a line's end or before
    # punctuation.
    assert "progresses on a public improvement contract. Payments must be based upon estimates of" in lines
    assert "retainage, due the contractor beginning 30 days after" in lines
    assert "days after the contracting agency approves the payment," in lines


def test_hb4010_margin_numbers_and_header_stay_out():
    data = _read("or_hb4010_introduced_p1.pdf")
    printed = pdfdata_to_text(data)
    assert re.search(r"^\s*1\s+A BILL FOR AN ACT", printed, re.MULTILINE)  # numbered in print
    new = READ(data, META)
    assert new.split("\n")[0] == "A BILL FOR AN ACT"
    assert not re.search(r"^\s*\d+\s", new, re.MULTILINE)
    assert "LC " not in new  # the draft number in the header
    # Only characters are taken out, and none outside the brackets but spacing.
    old = extract_sometimes_numbered_pdf(data, META)
    assert _is_subsequence(new, old)
    assert _outside_brackets(new) == _outside_brackets(old)


def test_hb4139_drops_whole_bracketed_subsections():
    data = _read("or_hb4139_introduced_p2.pdf")
    new = READ(data, META)
    lines = new.split("\n")
    i = lines.index("(a) A marijuana item; or")
    assert lines[i + 1] == "(b) An industrial hemp-derived cannabinoid product."
    assert "industrial hemp commodity" not in new
    assert "" not in lines[i - 3 : i + 3]


def test_hb4139_keeps_an_upright_bracket_in_a_chemical_name():
    data = _read("or_hb4139_introduced_p3.pdf")
    assert "c" in upright_brackets(data)
    new = READ(data, META)
    assert "6H- benzo[c]chromen-1-ol." in new
    assert "tetrahydro-6H-benzo[c]chromene-2-carboxylic acid." in new
    lines = new.split("\n")
    assert any(line.startswith("(21) “Licensee” means a person") for line in lines)
    assert "[(20)]" not in new
    assert new.count("[") == 2


def test_amendment_paper_drops_the_quoted_deletion_over_a_line_break():
    data = _read("or_hb4004_halnrw_amendment_11_p2.pdf")
    old = extract_sometimes_numbered_pdf(data, META)
    new = READ(data, META)
    assert "[and except as provided in\nsection 2 of this 2026 Act,]" in old
    assert (
        "“308A.707. (1) Notwithstanding ORS 308A.706, additional taxes shall be imposed on "
        "land that is" in new.split("\n")
    )
    assert _is_subsequence(new, old)


def test_amendment_paper_keeps_an_upright_quotation_bracket():
    data = _read("or_sb1563_sjud_amendment_2_p1.pdf")
    new = READ(data, META)
    assert "in the course of his employment[,]’ and" in new
    assert new == extract_sometimes_numbered_pdf(data, META)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Payments [shall] must be", "Payments must be"),
        ("the contractor[. The interest] beginning", "the contractor beginning"),
        ("after [receipt of the]\nreceiving", "after\nreceiving"),
        ("[(9)(a)] (10)(a) [The] A agency", "(10)(a) A agency"),
        ("performed; [or]\nnext", "performed;\nnext"),
        ("of the contract[.]; or", "of the contract; or"),
        ("approves the payment [is approved by the agency],", "approves the payment,"),
        ("after [the payment is\napproved by] the con-", "after the con-"),
        ("a\n[(b) gone:]\n[(A) gone too]\n(b) kept", "a\n(b) kept"),
        ("agencies[.]and of", "agencies and of"),
        ("matter [italic and bracketed] is existing law", "matter [italic and bracketed] is existing law"),
        ("no brackets here", "no brackets here"),
    ],
)
def test_drop_bracketed(text, expected):
    assert drop_bracketed(text) == expected


def test_drop_bracketed_keeps_listed_contents():
    assert drop_bracketed("benzo[c]chromen [shall] must", keep={"c"}) == "benzo[c]chromen must"


def test_unreadable_pdf_with_brackets_is_an_error():
    # The old reader's text has brackets only when the file is a real PDF; a file PyMuPDF cannot
    # open is an extraction error, never a quiet fallback to the bracketed text.
    def fake(data, metadata):
        return "Payments [shall] must"

    from openstates.fulltext.oregon import without_bracketed_text

    with pytest.raises(Exception):
        without_bracketed_text(fake)(b"not a pdf", META)


def test_oregon_reads_its_own_extractor_without_the_strike_reader():
    # Oregon prints no strike line: only the bracket reader wraps its usual PDF extractor.
    assert READ.__wrapped__ is extract_sometimes_numbered_pdf
    assert set(CONVERSION_FUNCTIONS["or"]) == {"application/pdf"}
