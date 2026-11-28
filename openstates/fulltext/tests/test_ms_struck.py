"""OPEN-950: Mississippi's HTML bill text, read without its struck-out words.

The fixtures are real, whole printings from the Mac's archive copy (2026 regular session):

* HB 1131, As Introduced: a penalty rewritten, "by a fine not exceeding * * * Five Thousand Dollars":
  the struck words are `<s><span style='display:none'>` runs beside `<u>` inserted words.
* HB 10, As Introduced: an `<s>` whose words are not hidden (a plain visible strike).
* HB 723, As Introduced: a struck percentage beside its replacement, and a hidden span with no `<s>`.
* HB 1126, Amendment No 1: a Word tracked deletion (`<del>`) beside a struck run.
* HB 1150, Amendment No 2: nothing struck.
"""

from pathlib import Path

import pytest

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.common import extract_line_numbered_pdf, extractor_for_element_by_xpath
from openstates.fulltext.ms import handle_mississippi_html

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:ms/government"}
# The reader before OPEN-950 (upstream's).
OLD = extractor_for_element_by_xpath('.//*[@class="WordSection1"]')
FIXTURES = (
    "ms_hb1131_introduced.html",
    "ms_hb10_introduced.html",
    "ms_hb723_introduced.html",
    "ms_hb1126_amendment_1.html",
    "ms_hb1150_amendment_2.html",
)


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _page(body: str) -> bytes:
    return f'<html><body><div class="WordSection1">{body}</div></body></html>'.encode()


def _flat(text: str) -> str:
    return " ".join(text.split())


def _is_subsequence(short: str, long: str) -> bool:
    rest = iter(long)
    return all(ch in rest for ch in short)


def test_reader_is_registered_for_html_and_the_pdf_reader_is_untouched():
    assert CONVERSION_FUNCTIONS["ms"]["text/html"] is handle_mississippi_html
    assert CONVERSION_FUNCTIONS["ms"]["application/pdf"] is extract_line_numbered_pdf


def test_hidden_struck_words_are_gone_and_the_inserted_words_stay():
    text = _flat(handle_mississippi_html(_read("ms_hb1131_introduced.html"), META))
    assert "by a fine not exceeding * * * Five Thousand Dollars" in text
    # the old reader printed the struck words beside the inserted ones
    old = _flat(OLD(_read("ms_hb1131_introduced.html"), META))
    assert "fine not exceeding * * *One Thousand Dollars ($1,000.00) Five Thousand Dollars" in old
    assert "One Thousand Dollars ($1,000.00)" not in text
    assert "Two Hundred Dollars" not in text
    assert "for not more than six (6) months" not in text


def test_a_visible_strike_is_removed_too():
    text = _flat(handle_mississippi_html(_read("ms_hb10_introduced.html"), META))
    assert "Secretary of State" not in text
    assert "the * * * Department of Agriculture shall issue a formal" in text


def test_a_struck_percentage_beside_its_replacement():
    text = _flat(handle_mississippi_html(_read("ms_hb723_introduced.html"), META))
    assert "fifty percent" not in text
    assert "A * * * seventy-five percent" in text


def test_a_hidden_span_outside_any_strike_element_is_removed():
    # as in the real amendment papers: the department<span hidden> may</span> shall issue
    data = _page("<p>the department<span style='display:none'> may</span> shall issue</p>")
    assert handle_mississippi_html(data, META) == "the department shall issue"
    assert "by the State" in _flat(handle_mississippi_html(_read("ms_hb723_introduced.html"), META))


def test_a_tracked_deletion_is_removed():
    text = _flat(handle_mississippi_html(_read("ms_hb1126_amendment_1.html"), META))
    assert "except for December" not in text
    assert "provisions of Section * * * 37-151-103(1) * * *." in text


def test_a_page_with_nothing_struck_reads_exactly_as_before():
    data = _read("ms_hb1150_amendment_2.html")
    assert handle_mississippi_html(data, META) == OLD(data, META)


@pytest.mark.parametrize("name", FIXTURES)
def test_the_new_text_is_the_old_text_with_characters_only_taken_out(name):
    data = _read(name)
    old, new = OLD(data, META), handle_mississippi_html(data, META)
    assert len(new) <= len(old)
    assert _is_subsequence("".join(new.split()), "".join(old.split()))


def test_the_text_between_and_after_struck_runs_is_kept_in_place():
    data = _page("<p>one <s><span style='display:none'>two</span></s>three <del>four</del> five<b>.</b></p>")
    assert handle_mississippi_html(data, META) == "one three  five."


def test_a_struck_element_inside_a_struck_element_is_taken_out_once():
    data = _page("<p>a <s>b <u><span style='display:none'>c</span></u> d</s> e</p>")
    assert handle_mississippi_html(data, META) == "a  e"


def test_inserted_text_is_not_removed():
    data = _page("<p>old <s>struck</s><u>inserted</u> <span class=msoIns><ins>tracked</ins></span></p>")
    assert handle_mississippi_html(data, META) == "old inserted tracked"


def test_a_page_without_exactly_one_word_section_is_an_error_as_before():
    with pytest.raises(AssertionError):
        handle_mississippi_html(b"<html><body><p>no section</p></body></html>", META)
