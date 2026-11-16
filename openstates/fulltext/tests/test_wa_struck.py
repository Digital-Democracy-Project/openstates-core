"""OPEN-580: Washington's HTML printings, read without struck-out words or their "((" "))"
marks, and the empty marks OPEN-560's PDF reader leaves taken out.

The fixtures are real, whole printings from the Mac's archive copy (2025-26 session):

* SB 6038, Bill (HTML and PDF): "two-year ((or)), four-year", "or" struck, "," underlined.
* HB 2091, Bill: a deletion over five paragraphs, "((" in the first and "))" in the last.
* SB 5405, Bill: struck amounts and section numbers, including "(a)(((ii)))(i)" and
  "Seattle-((-Tacoma--Bremerton))", whose hyphens inside the marks are not styled struck.
* HB 2421, Substitute Bill: nothing struck; "((" only in a chemical name.
"""

from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.al import read_without_struck_text
from openstates.fulltext.common import extract_line_numbered_pdf, extractor_for_element_by_xpath
from openstates.fulltext.wa import (
    STRUCK,
    drop_deletion_marks,
    handle_washington_html,
    without_deletion_marks,
)

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:wa/government"}
# The HTML reader before OPEN-580 (OPEN-212's).
OLD_HTML = extractor_for_element_by_xpath("//html", preserve_element_boundaries=True)
OLD_PDF = read_without_struck_text(extract_line_numbered_pdf)
HTML_FIXTURES = (
    "wa_sb6038_bill.html",
    "wa_hb2091_bill.html",
    "wa_sb5405_bill.html",
    "wa_hb2421_substitute.html",
)


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _html(name: str) -> list:
    return CONVERSION_FUNCTIONS["wa"]["text/html"](_read(name), META).split("\n")


def _changed(name: str) -> list:
    old = OLD_HTML(_read(name), META).split("\n")
    new = _html(name)
    return [line for line in old if line not in new], [line for line in new if line not in old]


def test_wa_reads_html_and_pdf_with_the_washington_readers():
    assert CONVERSION_FUNCTIONS["wa"]["text/html"] is handle_washington_html
    pdf = CONVERSION_FUNCTIONS["wa"]["application/pdf"]
    assert pdf.__wrapped__.__wrapped__ is extract_line_numbered_pdf


def test_sb6038_struck_word_and_its_marks_are_gone():
    removed, added = _changed("wa_sb6038_bill.html")
    assert removed == [
        "(g) Not require a two-year ((or)), four-year, or postgraduate college degree as the only "
        "way to demonstrate qualifications for the role unless that degree is required by law for "
        "an employee to perform the essential functions of a classification; and"
    ]
    assert added == [
        "(g) Not require a two-year, four-year, or postgraduate college degree as the only way to "
        "demonstrate qualifications for the role unless that degree is required by law for an "
        "employee to perform the essential functions of a classification; and"
    ]


def test_sb6038_html_and_pdf_now_read_the_same_sentence():
    pdf = CONVERSION_FUNCTIONS["wa"]["application/pdf"](_read("wa_sb6038_bill.pdf"), META)
    assert "two-year, four-year, or postgraduate" in pdf
    assert "(( ))" in OLD_PDF(_read("wa_sb6038_bill.pdf"), META)
    assert "((" not in pdf
    assert "two-year, four-year, or postgraduate" in "\n".join(_html("wa_sb6038_bill.html"))


def test_hb2091_deletion_over_five_paragraphs_goes_whole():
    removed, added = _changed("wa_hb2091_bill.html")
    assert removed == [
        "RCW 41.56.065 applies to ((the following)) employers subject to this chapter((:",
        "(1) Western Washington University;",
        "(2) Central Washington University;",
        "(3) Eastern Washington University; and",
        "(4) The Evergreen State College)).",
    ]
    assert added == ["RCW 41.56.065 applies to employers subject to this chapter."]


def test_sb5405_amounts_section_numbers_and_leaders():
    text = "\n".join(_html("wa_sb5405_bill.html"))
    assert "(i) $2,193,000 for estates of decedents dying before January 1, 2026; and" in text
    assert "dying in calendar year 2026 and each calendar year thereafter" in text
    assert "the amount in (a)(i) of this subsection" in text
    assert "multiplying $2,193,000 by the sum" in text
    # The hyphens between the struck "Tacoma-" and "Bremerton" are inside the marks but not
    # styled struck; the one before "((" is outside them and stays, as the printing has it.
    assert "for the Seattle- metropolitan area" in text
    for gone in ("One million five hundred thousand", "two million dollars", "Bremerton", "(("):
        assert gone not in text


def test_a_printing_with_nothing_struck_reads_exactly_as_before():
    data = _read("wa_hb2421_substitute.html")
    new = CONVERSION_FUNCTIONS["wa"]["text/html"](data, META)
    assert new == OLD_HTML(data, META)
    assert "6PPD-quinone (2-((4-methylpentan-2-yl)amino)" in new


def test_only_characters_are_taken_out_and_only_struck_ones_or_marks():
    for name in HTML_FIXTURES:
        old = "".join(OLD_HTML(_read(name), META).split())
        new = "".join("".join(_html(name)).split())
        rest = iter(old)
        assert all(ch in rest for ch in new), name
        assert STRUCK not in new, name


def test_marks_are_taken_out_only_around_a_strike_or_nothing():
    assert drop_deletion_marks("the ((" + STRUCK + ")) new") == "the new"
    assert drop_deletion_marks("two-year ((" + STRUCK + ")), four") == "two-year, four"
    assert drop_deletion_marks("word((" + STRUCK + "))new") == "wordnew"
    assert drop_deletion_marks("by ((" + STRUCK + "\n" + STRUCK + ")) the") == "by the"
    assert drop_deletion_marks("rule.\n((    )) (18) next") == "rule.\n(18) next"
    assert drop_deletion_marks("$5 ((" + STRUCK + " . . . . )) Next") == "$5 Next"
    # Not a deletion: text inside, or leaders with no strike beside them.
    for kept in ("(2-((4-methylpentan-2-yl)amino)", "(((a / b)^(1)))", "(( . . . . ))", "((a))"):
        assert drop_deletion_marks(kept) == kept
    # Words not styled struck stay, even beside a strike inside the marks.
    beside = "((" + STRUCK + " kept 2))"
    assert drop_deletion_marks(beside) == beside


def test_pdf_wrapper_drops_only_the_empty_marks():
    pdf = without_deletion_marks(lambda data, metadata: "age (( )) 20 and ((older))")
    assert pdf(b"", META) == "age 20 and ((older))"


def test_no_other_state_reads_with_the_washington_readers():
    for state, funcs in CONVERSION_FUNCTIONS.items():
        modules = {getattr(func, "__module__", None) for func in funcs.values()}
        assert ("openstates.fulltext.wa" in modules) == (state == "wa"), state
