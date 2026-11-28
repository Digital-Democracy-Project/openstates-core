"""OPEN-949: Mississippi's pre-diff cleaner drops the stage line, the bare bill-number line, the "* * *" mark and blank
lines.

Mississippi's HTML opens with a header block whose stage line ("(As Passed the House)") and bill number change with
every version, and its PDFs carry "* * *" marks for text left out; 1,174 of the 1,801 stored HTML comparisons changed
nothing else. The fixtures in fixtures/open949_ms are whole real documents from the Mac replica (Mississippi 2026
regular session), taken with the document each was compared against.
"""

import difflib
import pathlib

import pytest

from openstates.cli.text_extract import _compare_texts, apply_prediff_cleaning
from openstates.ddp_states import ms, state_rule

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "open949_ms"


def _read(name):
    return (FIXTURES / name).read_text()


def _changed_lines(diff):
    return [line for line in diff.split("\n") if line[:1] in ("+", "-") and line[:3] not in ("+++", "---")]


def _compare(prior, raw, media_type="text/html"):
    return _compare_texts(
        prior, raw, jurisdiction_name="Mississippi", is_bill=True,
        prior_media_type=media_type, cur_media_type=media_type,
    )


def _plain(prior, raw):
    return "\n".join(difflib.unified_diff(prior.splitlines(), raw.splitlines(), lineterm=""))


def test_a_version_that_only_gains_its_stage_line_compares_as_unchanged():
    prior, raw = _read("hb352_html_introduced.txt"), _read("hb352_html_passed_house.txt")
    # Without the cleaner the stage line is the whole change (and so are the blank lines around it).
    assert "+(As Passed the House)" in _changed_lines(_plain(prior, raw))
    assert _compare(prior, raw) == ""


def test_a_real_edit_is_all_that_is_left():
    prior, raw = _read("hcr21_html_introduced.txt"), _read("hcr21_html_adopted_house.txt")
    plain = _changed_lines(_plain(prior, raw))
    assert "+(As Adopted by House)" in plain
    # Only the two body lines whose words changed (the date) remain; the author, the bill number and the rest stay out
    # of the diff because they are the same on both sides, and the stage line no longer shows.
    assert _changed_lines(_compare(prior, raw)) == [
        "-held in the Second Floor Rotunda of the New Capitol Building, on Tuesday,",
        "-January 27, 2026, at 5:00 p.m., to hear the State of the State address of",
        "+held in the Second Floor Rotunda of the New Capitol Building, on Wednesday,",
        "+February 4, 2026, at 5:00 p.m., to hear the State of the State address of",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "(As Passed the House)",
        "(As Passed the Senate)",
        "(As Sent to Governor)",
        "(As Adopted by House)",
        "(As Adopted by Senate)",
        "(As Adopted by House and Senate)",
        "(As Adopted by Senate and House)",
        "(COMMITTEE SUBSTITUTE)",
        "House Bill 1606",
        "Senate Bill 3073",
        "House Resolution 7",
        "Senate Resolution 12",
        "House Concurrent Resolution 21",
        "Senate Concurrent Resolution 5",
        "* * *",
    ],
)
def test_the_printed_shapes_are_dropped(line):
    assert ms._clean_ms_text(f"before\n{line}\nafter") == "before\nafter"


@pytest.mark.parametrize(
    "line",
    [
        "House Bill No. 1020, 2023",  # a citation in a sentence
        "pursuant to House Bill 1020",
        "House Bill No. 1",
        "House Bill 0",
        "House Bill",
        "Bill 342",  # a header the site wrapped onto two lines: kept, not guessed at
        "House",
        "(As",
        "Sent to Governor)",
        "(As Amended)",
        "(As Passed the House) and",
        "(as passed the house)",
        "(COMMITTEE SUBSTITUTE) 1",
        "(COMMITTEE SUBSTITUTE",
        "As Passed the House)",
        "  (As Passed the House)",  # matched as printed: no padded one was seen
        "(As Passed the House) ",
        "  House Bill 1606",
        "* * * A cottage food operation must comply",
        "food operation complies with this section * * *.",
        "(a)  * * *",
        "***",
        "* * * *",
        "1985.",
        "5",
        "(b)",
        "................................................ $",
        "SECTION 3.",
    ],
)
def test_close_shapes_are_kept(line):
    assert ms._clean_ms_text(f"before\n{line}\nafter") == f"before\n{line}\nafter"


def test_blank_lines_go_and_every_other_line_stays_byte_for_byte():
    text = "\n\n     SECTION\n1.  The  Union  County\n \n\t\nMuseum is designated.  \n\n"
    assert ms._clean_ms_text(text) == "     SECTION\n1.  The  Union  County\nMuseum is designated.  "


def test_a_bill_cited_in_a_sentence_stays():
    raw = _read("cites_house_bill_no.txt")
    assert "one (1) full-time criminal investigator pursuant to House Bill No. 1020, 2023" in raw
    # The document's own two header lines go (the bill number and the stage); every other non-blank line is kept.
    header = ("House Bill 1927", "(As Sent to Governor)")
    expected = [line for line in raw.split("\n") if line.strip() and line not in header]
    assert ms._clean_ms_text(raw).split("\n") == expected
    assert len(expected) == len([line for line in raw.split("\n") if line.strip()]) - 2


def test_the_star_marks_of_a_pdf_go_and_the_text_around_them_stays():
    raw = _read("pdf_stars_passed_house.txt")
    lines = raw.split("\n")
    assert lines.count("* * *") >= 1 and any("* * *" in line and line != "* * *" for line in lines)
    cleaned = ms._clean_ms_text(raw).split("\n")
    assert "* * *" not in cleaned
    assert cleaned == [line for line in lines if line != "* * *" and line.strip()]
    # the inline marks are body text
    assert any(line.startswith("75-29-951.   (1)   * * * A cottage food") for line in cleaned)


def test_the_cleaner_runs_through_the_state_file_for_mississippi_only():
    text = "(As Passed the Senate)\nbody\n* * *\n"
    prior, raw = apply_prediff_cleaning(
        text, text, jurisdiction_name="Mississippi", is_bill=True,
        prior_media_type="text/html", cur_media_type="text/html",
    )
    assert prior == raw == "body"
    other = apply_prediff_cleaning(
        text, text, jurisdiction_name="Alaska", is_bill=True,
        prior_media_type="text/html", cur_media_type="text/html",
    )
    assert other == (text, text)
    assert state_rule("ms", "clean_text") is ms.clean_text
