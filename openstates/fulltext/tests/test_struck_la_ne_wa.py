"""OPEN-560: Louisiana, Nebraska and Washington PDFs, read with struck-out words left out by
Alabama's reader (fulltext/al.py).

The fixtures are real pages, each cut from the published PDF with PyMuPDF (pdftotext reads the
cut page exactly as it reads that page in the whole file):

* Louisiana SB 495 (2026), Engrossed, page 6: "fifty" is struck and "two hundred" underlined
  ("in excess of fifty two hundred dollars", the figure LegBot got wrong).
* Nebraska LB 778 (109th), Final Reading, page 2: "2023" and "2024" are struck after the
  underlined "2027" and "2028"; page 1 has nothing struck.
* Louisiana HB 434 (2026), Act 645, the whole two-page file: "the written recommendation of the local" and
  more are struck on page 1; page 2 has the signature rule after "APPROVED:" at 0.72 of the
  line height, which is not a strike.
* Louisiana SB 116 (2026), Original, page 13: the congressional map, on a page turned 90
  degrees, whose parish borders cross the upright parish labels.
* Washington SB 5681 (2025-26), Substitute Bill, page 1: "21" and "access" are struck inside
  Washington's "((...))" deletion marks, with "20" and "inclusion" underlined.

OPEN-584 adds North Carolina, read the same way:

* North Carolina HB 613 (2025), Edition 2, page 1: "renewal electronically to an e-mail address
  provided by the owner of the vehicle." is struck and "renewal." underlined, in a section of
  existing law "reads as rewritten".
* North Carolina SB 809 (2025), Edition 3, page 1: words underlined, nothing struck.

OPEN-591 adds Nevada, read the same way. Nevada prints removed law inside brackets and struck
through, brackets included. Its pages are cut as above, then their fonts subset (PyMuPDF
`subset_fonts`) to keep the files small; pdftotext reads each cut page exactly as it reads that
page in the whole file, before and after the struck words are removed:

* Nevada AB 4 (2025, 36th Special), First Reprint, page 6: "[(c)]" is struck before the new
  "(e)" ("[(c)] (e) “Health care facility” means ...", the line the ticket reports).
* Nevada AB 4, First Reprint, page 28: "[suspends]", "[,]" and a whole struck sub-section
  ("[permitting the child to drive a motor vehicle: (a) To and from work ...]") over 8 lines.
* Nevada AB 4, First Reprint, page 2: nothing struck.
"""

import difflib
import inspect
import re
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.al import _struck_runs, handle_alabama_pdf, without_struck_text
from openstates.fulltext.common import (
    extract_line_numbered_pdf,
    extract_sometimes_numbered_pdf,
)

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:la/government"}


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _lines(text: str) -> list:
    return [" ".join(line.split()) for line in text.split("\n")]


def _pdf(state: str):
    return CONVERSION_FUNCTIONS[state]["application/pdf"]


def test_louisiana_sb495_reads_two_hundred_not_fifty_two_hundred():
    data = _read("la_sb495_engrossed_p6.pdf")
    old = "\n".join(_lines(extract_sometimes_numbered_pdf(data, META)))
    new = _lines(_pdf("la")(data, META))
    assert "fifty two hundred" in old  # the defect the ticket reports
    assert "fifty" not in "\n".join(new)
    assert (
        "Paragraph, having an attributable monetary value in excess of two hundred"
        in new
    )


def test_nebraska_lb778_reads_the_new_years_only():
    data = _read("ne_lb778_final_reading_p2.pdf")
    old = _lines(extract_sometimes_numbered_pdf(data, META))
    new = _lines(_pdf("ne")(data, META))
    assert "(4) From July 1, 2027 2023, to June 30, 2028 2024, a municipality" in old
    assert "(4) From July 1, 2027 , to June 30, 2028 , a municipality" in new


def test_washington_sb5681_reads_the_new_words_only():
    data = _read("wa_sb5681_s_p1.pdf")
    old = "\n".join(_lines(extract_line_numbered_pdf(data, META)))
    new = "\n".join(_lines(_pdf("wa")(data, META)))
    assert "Clients age ((21)) 20 and older" in old
    assert "community ((access)) inclusion" in old.replace("\n", " ")
    # Washington's "((" "))" marks are printed, not struck, so the strike reader leaves them
    # around the gap; OPEN-580 then takes the empty pair out (fulltext/wa.py).
    assert "Clients age (( )) 20 and older" in "\n".join(
        _lines(_pdf("wa").__wrapped__(data, META))
    )
    assert "Clients age 20 and older" in new
    assert "access" not in new
    assert "inclusion" in new


def test_north_carolina_hb613_reads_the_new_words_only():
    data = _read("nc_hb613_edition2_p1.pdf")
    old = "\n".join(_lines(extract_sometimes_numbered_pdf(data, META)))
    new = "\n".join(_lines(_pdf("nc")(data, META)))
    assert "notice of renewal electronically to an e-mail address" in old
    assert "of the vehicle.renewal." in old
    assert "electronically" not in new
    assert "vehicle any required notice of\nrenewal." in new


def test_nevada_ab4_reads_the_new_letter_only():
    data = _read("nv_ab4_r1_p6.pdf")
    old = "\n".join(_lines(extract_sometimes_numbered_pdf(data, META)))
    new = "\n".join(_lines(_pdf("nv")(data, META)))
    assert "[(c)] (e) “Health care facility” means" in old
    assert "\n(e) “Health care facility” means" in new
    assert "[" not in new and "]" not in new


def test_nevada_whole_struck_sub_section_is_taken_out():
    # Nevada's struck text runs over whole lines; those lines drop out of the reading, and every
    # other line comes back exactly as before.
    data = _read("nv_ab4_r1_p28.pdf")
    old = _lines(extract_sometimes_numbered_pdf(data, META))
    new = _lines(_pdf("nv")(data, META))
    assert sum(line.count("[") for line in old) == 3
    assert not [line for line in new if "[" in line or "]" in line]
    changes = [
        (old[i1:i2], new[j1:j2])
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, old, new, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]
    assert changes == [
        (
            [
                "3. If the juvenile court [suspends] finds that the suspension of",
                "the driver’s license of a child pursuant to this section [,] is not in the",
            ],
            [
                "3. If the juvenile court finds that the suspension of",
                "the driver’s license of a child pursuant to this section is not in the",
            ],
        ),
        (
            [
                "license pursuant to NRS 483.490 . [permitting the child to drive a",
                "motor vehicle:",
                "(a) To and from work or in the course of his or her work, or",
                "both;",
                "(b) To and from school; or",
                "(c) To acquire supplies of medicine or food or receive regularly",
                "scheduled medical care for himself, herself or a member of his or",
                "her immediate family.]",
            ],
            ["license pursuant to NRS 483.490 ."],
        ),
    ]
    # Characters only taken out, never added, over the whole page (spacing aside).
    rest = iter("".join(old).replace(" ", ""))
    assert all(ch in rest for ch in "".join(new).replace(" ", ""))


@pytest.mark.parametrize(
    "state,name,extract",
    [
        ("la", "la_sb495_engrossed_p6.pdf", extract_sometimes_numbered_pdf),
        ("nc", "nc_hb613_edition2_p1.pdf", extract_sometimes_numbered_pdf),
        ("ne", "ne_lb778_final_reading_p2.pdf", extract_sometimes_numbered_pdf),
        ("nv", "nv_ab4_r1_p6.pdf", extract_sometimes_numbered_pdf),
        ("wa", "wa_sb5681_s_p1.pdf", extract_line_numbered_pdf),
    ],
)
def test_only_characters_are_taken_out(state, name, extract):
    # Each line read now is its line read the old way with characters taken out, never added
    # (spacing aside), and at least one line lost something.
    data = _read(name)
    old = _lines(extract(data, META))
    new = _lines(_pdf(state)(data, META))
    assert len(new) == len(old)
    for before, after in zip(old, new):
        rest = iter(before.replace(" ", ""))
        assert all(ch in rest for ch in after.replace(" ", "")), (before, after)
    assert old != new


# Every line that changes on each fixture page, old reading then new, spacing aside; every other
# line must come back exactly as before. The subsequence check above cannot see an unstruck
# neighbour being eaten (a redaction box a few points too wide still only takes characters
# out); pinning the whole page can (OPEN-560 review, 2026-11-15).
EXPECTED_CHANGES = {
    "la_sb495": (
        "la",
        "la_sb495_engrossed_p6.pdf",
        extract_sometimes_numbered_pdf,
        [
            (
                "(iv) Any person for the purpose of funding an expenditure to support or",
                "(iv)",
            ),
            (
                "oppose a proposition or question submitted to the voters. Any recognized political",
                "Any recognized political",
            ),
            (
                "Paragraph, having an attributable monetary value in excess of fifty two hundred",
                "Paragraph, having an attributable monetary value in excess of two hundred",
            ),
            (
                "employees, the value of which services exceeds fifty two hundred dollars, such",
                "employees, the value of which services exceeds two hundred dollars, such",
            ),
            (
                "candidate, the chairman of the accepting political committee, or accepting person",
                "candidate, the chairman of the accepting committee, or accepting person",
            ),
        ],
    ),
    # Louisiana's struck words in SB 495 sit between wide spaces, so a box a few points too wide
    # reaches no neighbour there; in HB 434 "the written recommendation of the local" is struck
    # right after "upon", and a box 4 points too wide eats its "n".
    "la_hb434": (
        "la",
        "la_hb434_act645.pdf",
        extract_sometimes_numbered_pdf,
        [
            (
                "probationary term, the superintendent city, parish, or other local public school board",
                "probationary term, the superintendent",
            ),
            (
                "may dismiss or discharge any operator upon the written recommendation of the local",
                "may dismiss or discharge any operator upon",
            ),
            (
                "superintendent of schools accompanied by valid reasons therefor providing the",
                "providing the",
            ),
        ],
    ),
    "nc_hb613": (
        "nc",
        "nc_hb613_edition2_p1.pdf",
        extract_sometimes_numbered_pdf,
        [
            (
                "vehicle any required notice of renewal electronically to an e-mail address provided by the owner",
                "vehicle any required notice of",
            ),
            ("of the vehicle.renewal.", "renewal."),
        ],
    ),
    "ne_lb778": (
        "ne",
        "ne_lb778_final_reading_p2.pdf",
        extract_sometimes_numbered_pdf,
        [
            (
                "(4) From July 1, 2027 2023, to June 30, 2028 2024, a municipality",
                "(4) From July 1, 2027 , to June 30, 2028 , a municipality",
            ),
            (
                "Center Financing Act only if such municipality (a) partners with a",
                "Center Financing Act only if such municipality",
            ),
            (
                "certified creative district and (b) is not prohibited from receiving a",
                "is not prohibited from receiving a",
            ),
            (
                "of section 82-312. After June 30, 2028 2024, this subsection no longer",
                "of section 82-312. After June 30, 2028 , this subsection no longer",
            ),
        ],
    ),
    "nv_ab4_r1_p6": (
        "nv",
        "nv_ab4_r1_p6.pdf",
        extract_sometimes_numbered_pdf,
        [
            (
                "[(c)] (e) “Health care facility” means a facility licensed",
                "(e) “Health care facility” means a facility licensed",
            ),
        ],
    ),
    "wa_sb5681": (
        "wa",
        "wa_sb5681_s_p1.pdf",
        extract_line_numbered_pdf,
        [
            (
                "(1) Clients age ((21)) 20 and older who are receiving employment",
                "(1) Clients age 20 and older who are receiving employment",
            ),
            (
                "((access)) inclusion program after nine months of enrollment in an",
                "inclusion program after nine months of enrollment in an",
            ),
        ],
    ),
}


@pytest.mark.parametrize("fixture", sorted(EXPECTED_CHANGES))
def test_exactly_the_struck_words_are_taken_out(fixture):
    state, name, extract, expected = EXPECTED_CHANGES[fixture]
    data = _read(name)
    old = _lines(extract(data, META))
    new = _lines(_pdf(state)(data, META))
    assert len(new) == len(old)
    assert [(a, b) for a, b in zip(old, new) if a != b] == expected


def test_washington_removes_only_text_inside_its_deletion_marks():
    # Washington prints every deletion inside "((" "))": with the inside of each pair emptied,
    # the old and new lines are the same characters (spacing aside). A removal outside the
    # marks, of any size, fails this.
    def outside_marks(line: str) -> str:
        return re.sub(r"\(\(.*?\)\)", "(())", line.replace(" ", ""))

    data = _read("wa_sb5681_s_p1.pdf")
    old = _lines(extract_line_numbered_pdf(data, META))
    # The strike reader alone, before OPEN-580 takes the emptied marks out.
    new = _lines(_pdf("wa").__wrapped__(data, META))
    assert len(new) == len(old)
    assert [outside_marks(line) for line in new] == [outside_marks(line) for line in old]
    assert new != old


def test_louisiana_signature_rule_is_not_read_as_a_strike():
    # The rule after "APPROVED:" on the last page of a Louisiana Act sits at 0.72 of the line's
    # height, just above the strike band's top (0.70). It must not be taken for a strike: no
    # struck run on that line, so a signature or fill-in rule never removes what it crosses.
    doc = fitz.open(stream=_read("la_hb434_act645.pdf"), filetype="pdf")
    page = doc[1]
    rules = [d["rect"] for d in page.get_drawings() if d["rect"].height < 2.0]
    [approved] = [
        span
        for block in page.get_text("rawdict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if "".join(c["c"] for c in span["chars"]).startswith("APPROVED:")
    ]
    x0, top, x1, bottom = approved["bbox"]
    crossing = [
        ((rule.y0 + rule.y1) / 2 - top) / (bottom - top)
        for rule in rules
        if rule.x1 > x0 and rule.x0 < x1
    ]
    assert any(0.71 < pos < 0.73 for pos in crossing)  # the fixture still shows the case
    assert not [run for run in _struck_runs(page, rules) if top <= run[2] <= bottom]


def test_louisiana_map_labels_are_not_read_as_struck():
    # A redistricting map on a page turned 90 degrees: the parish labels run up the page and the
    # parish borders cross them where a strike would sit if the text ran left to right. Found by
    # the whole-archive comparison; only left-to-right text is measured, so the page is left
    # alone.
    data = _read("la_sb116_original_p13_map.pdf")
    assert without_struck_text(data) is data
    text = _pdf("la")(data, META)
    for parish in ("Allen", "Calcasieu", "Caldwell", "Orleans"):
        assert parish in text


def test_a_page_with_nothing_struck_is_read_exactly_as_before():
    data = _read("ne_lb778_final_reading_p1.pdf")
    assert without_struck_text(data) is data
    assert _pdf("ne")(data, META) == extract_sometimes_numbered_pdf(data, META)


def test_a_north_carolina_page_with_underlines_only_is_read_exactly_as_before():
    # North Carolina underlines inserted words at 0.93 of the line height, outside the strike
    # band; a page with underlines and no strike keeps every word.
    data = _read("nc_sb809_edition3_p1.pdf")
    assert without_struck_text(data) is data
    assert _pdf("nc")(data, META) == extract_sometimes_numbered_pdf(data, META)


def test_a_nevada_page_with_nothing_struck_is_read_exactly_as_before():
    data = _read("nv_ab4_r1_p2.pdf")
    assert without_struck_text(data) is data
    assert _pdf("nv")(data, META) == extract_sometimes_numbered_pdf(data, META)


def test_a_file_pymupdf_cannot_open_is_an_extraction_error():
    # As for Alabama: no quiet fallback that would bring the struck words back.
    for state in ("la", "nc", "ne", "nv", "wa"):
        with pytest.raises(Exception):
            _pdf(state)(b"%PDF-1.7\n" + b"\x00" * 64, META)


def test_only_these_six_states_read_pdfs_without_struck_text():
    # Each of the five keeps its own text extractor underneath, and no other state's
    # reader or media type changes.
    # (Washington's is wrapped twice: OPEN-580 takes the emptied "((" "))" marks out.)
    wrapped = {
        state: inspect.unwrap(funcs["application/pdf"])
        for state, funcs in CONVERSION_FUNCTIONS.items()
        if hasattr(funcs.get("application/pdf"), "__wrapped__")
    }
    assert wrapped == {
        "la": extract_sometimes_numbered_pdf,
        "nc": extract_sometimes_numbered_pdf,
        "ne": extract_sometimes_numbered_pdf,
        "nv": extract_sometimes_numbered_pdf,
        "wa": extract_line_numbered_pdf,
    }
    assert CONVERSION_FUNCTIONS["al"]["application/pdf"] is handle_alabama_pdf
    assert set(CONVERSION_FUNCTIONS["la"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["nc"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["ne"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["nv"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["wa"]) == {"text/html", "application/pdf"}
