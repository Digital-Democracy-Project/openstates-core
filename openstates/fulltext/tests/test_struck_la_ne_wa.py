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

OPEN-601 adds Georgia, read the same way:

* Georgia SR 1 (2026 special session), As introduced (LC 33 9932), pages 2 to 4: Senate rules
  rewritten with whole sentences struck and the new words underlined ("five (5) days" struck
  before the underlined "twenty-four (24)", "no" struck before "No", "ten (10)" before
  "three (3)").
* Georgia SB 3 (2026 special session), As introduced (LC 47 4392), page 3: "United States
  Senate, Governor, Lieutenant Governor, Secretary of State, Attorney General," struck from the
  ballot-order list.

OPEN-591 adds Nevada, read the same way. Nevada prints removed law inside brackets and struck
through, brackets included. Its pages are cut as above, then their fonts subset (PyMuPDF
`subset_fonts`) to keep the files small; pdftotext reads each cut page exactly as it reads that
page in the whole file, before and after the struck words are removed:

* Nevada AB 4 (2025, 36th Special), First Reprint, page 6: "[(c)]" is struck before the new
  "(e)" ("[(c)] (e) “Health care facility” means ...", the line the ticket reports).
* Nevada AB 4, First Reprint, page 28: "[suspends]", "[,]" and a whole struck sub-section
  ("[permitting the child to drive a motor vehicle: (a) To and from work ...]") over 8 lines.
* Nevada AB 4, First Reprint, page 2: nothing struck.

OPEN-836 adds North Dakota, read the same way. North Dakota strikes removed law at 0.55 of the
line height and underlines new words at 0.85, in every version kind, the enrollment included.
Pages cut and font-subset as Nevada's, from the PDFs on ndlegis.gov (byte-identical to the
archive's copies):

* North Dakota HB 1011 (69th), First Engrossment, page 2: the fee table, "$200.00" struck before
  the underlined "$240" (the ticket's example); page 1 has underlines and nothing struck.
* North Dakota HB 1205, Introduced, page 2: struck sentences ("Neither the parent nor the ...").
* North Dakota HB 1084, Introduced, page 1: a whole struck subsection (3), and the subsections
  after it renumbered ("4.3." becomes "3.").
* North Dakota HB 1194, First Engrossment, page 1: "A person" struck on its own line, the
  underlined "An individual" inserted, "personindividual" becomes "individual".
* North Dakota SB 2159, Enrollment, page 1: "TheIf" becomes "If", "may not conduct" becomes "may
  conduct".
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


# Georgia strikes whole printed lines, and a line left with only its line number is dropped, so
# the old and new readings do not have the same number of lines: each change is pinned as the
# run of old lines and the run of new lines that replaces it (spacing aside); every other line
# must come back exactly as before.
GA_EXPECTED_CHANGES = {
    "ga_sr1_introduced_p2_p4.pdf": [
        (
            [
                "on the legislative day prior to consideration of the same during the first thirty-six (36) days",
                "of any regular session. During the thirty-seventh (37th) through the fortieth (40th) days of",
                "any regular session, such reports shall be printed and distributed to the Senators at least two",
            ],
            ["at least two"],
        ),
        (
            [
                "After a Committee of Conference has been in existence for five (5) days twenty-four (24)"
            ],
            [
                "After a Committee of Conference has been in existence for twenty-four (24)"
            ],
        ),
        (
            [
                "contrary to the Rules of the Senate; provided that, after the thirty-fifth (35th) legislative"
            ],
            ["contrary to the Rules of the Senate"],
        ),
        (
            [
                "day, the above motions may be made and passed at any time, but not more often than every",
                'twenty-four hours."',
            ],
            ['."'],
        ),
        (
            [
                "as otherwise provided in paragraph (2) of this subsection, no No bill or resolution"
            ],
            [
                "as otherwise provided in paragraph (2) of this subsection, no bill or resolution"
            ],
        ),
        (
            [
                "Any general bill or resolution shall be automatically passed to a second reading Every",
                "general bill and resolution shall be read a second time on the same legislative day following",
                "the day that the bill or resolution is reported by the committee to which it was referred.",
                "Except that after the thirty-fifth (35th) day of any regular session, every bill and resolution",
                "shall be read a second time on the same legislative day that the bill or resolution is reported",
                "by the committee to which it was referred. No debate shall be admitted upon any bill at the",
            ],
            [
                "Every",
                "general bill and resolution shall be read a second time on the same legislative day",
                "that the bill or resolution is reported by the committee to which it was referred.",
                "No debate shall be admitted upon any bill at the",
            ],
        ),
        (
            [
                'the proponents and opponents shall each be limited to ten (10) three (3) minutes."'
            ],
            [
                'the proponents and opponents shall each be limited to three (3) minutes."'
            ],
        ),
        (
            [
                "otherwise than in the order and manner fixed by such calendar, except by a three-fourths"
            ],
            [
                "otherwise than in the order and manner fixed by such calendar, except by a"
            ],
        ),
        (
            [
                "members elected to the Senate. This Rule may not be suspended by a two-thirds"
            ],
            ["members elected to the Senate. This Rule may not be suspended by a"],
        ),
    ],
    "ga_sb3_introduced_p3.pdf": [
        (
            ["(A) The contest at the top of a ballot; and"],
            ["(A) The contest at the top of a ballot;"],
        ),
        (
            [
                "selected pursuant to subsection (d) of this Code section: United States Senate,",
                "Governor, Lieutenant Governor, Secretary of State, Attorney General, State School",
            ],
            [
                "selected pursuant to subsection (d) of this Code section:",
                "State School",
            ],
        ),
    ],
}


@pytest.mark.parametrize("name", sorted(GA_EXPECTED_CHANGES))
def test_georgia_exactly_the_struck_words_are_taken_out(name):
    data = _read(name)
    old = _lines(extract_sometimes_numbered_pdf(data, META))
    new = _lines(_pdf("ga")(data, META))
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    changes = [
        (old[i1:i2], new[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
    assert changes == GA_EXPECTED_CHANGES[name]
    # Over the whole page, characters are only taken out, never added (spacing aside).
    rest = iter("".join(old).replace(" ", ""))
    assert all(ch in rest for ch in "".join(new).replace(" ", ""))


def test_georgia_sr1_keeps_the_new_underlined_words():
    # The ticket's three examples: the struck words go, the underlined new words stay.
    data = _read("ga_sr1_introduced_p2_p4.pdf")
    old = "\n".join(_lines(extract_sometimes_numbered_pdf(data, META)))
    new = "\n".join(_lines(_pdf("ga")(data, META)))
    assert "in existence for five (5) days twenty-four (24)" in old
    assert "in existence for twenty-four (24)" in new
    assert "five (5) days" not in new
    assert "this subsection, no No bill" in old
    assert "this subsection, no bill" in new
    assert "limited to three (3) minutes" in new
    assert "ten (10)" not in new
    assert "automatically passed to a second reading" not in new


def test_georgia_sb3_drops_the_struck_offices_from_the_ballot_order():
    data = _read("ga_sb3_introduced_p3.pdf")
    new = "\n".join(_lines(_pdf("ga")(data, META)))
    # The old order is struck; the new order, printed further down the page and underlined,
    # names the same offices and is kept.
    assert "Code section: United States Senate," not in new
    assert "Secretary of State, Attorney General, State School" not in new
    assert "Code section:\nState School" in new
    assert (
        "United States Representative, Governor, Lieutenant Governor, Secretary of"
        in new
    )


# North Dakota (OPEN-836): each change pinned as for Georgia, since whole struck lines drop out.
ND_EXPECTED_CHANGES = {
    "nd_hb1011_engrossment1_p2.pdf": [
        (
            [
                "a. For each broker-dealer $200.00$240",
                "b. For each agent $60.00$75",
                "c. For each investment adviser or federal covered adviser $100.00$120",
                "d. For each investment adviser representative $50.00$60",
            ],
            [
                "a. For each broker-dealer $240",
                "b. For each agent $75",
                "c. For each investment adviser or federal covered adviser $120",
                "d. For each investment adviser representative $60",
            ],
        )
    ],
    "nd_hb1205_introduced_p2.pdf": [
        (["approved location. Neither the parent nor the"], ["approved location."]),
        (
            [
                "prosecution under sections 14-07-15 and 14-09-22 for leaving an abandoned",
                "infant.",
                "3. An infant left under this section must be accepted at an approved location shall accept",
                "an infant left under this section. The",
            ],
            [
                "prosecution under sections 14-07-15 and 14-09-22",
                ".",
                "3. An infant left under this section must be accepted at an approved location",
                ".",
            ],
        ),
        (
            [
                "regarding the parents and shall provide the parent or the agent with a medical history"
            ],
            [
                "regarding the parents and shall provide the parent or agent with a medical history"
            ],
        ),
        (
            [
                "accepting thean infant has the infant's medical history, the hospital is not required to",
                "provide the parent or the agent with a medical history form. Neither theA parent nor",
                "theor agent is not required to provide any information.",
                "4.5. IfExcept as provided in subsection 6, if an infant is left at a hospital, the hospital shall",
                "provide the parent or the agent with a numbered identification bracelet to link the",
                "parent or the agent to the infant, unless due to birth of the infant, the infant and parent",
                "currentlyalready have an identification bracelet. Possession of an identification",
                "bracelet does not entitle the bracelet holder to take custody of the infant on demand. If",
            ],
            [
                "accepting an infant has the infant's medical history, the hospital is not required to",
                "provide the parent or agent with a medical history form. A parent",
                "or agent is not required to provide any information.",
                "5. Except as provided in subsection 6, if an infant is left at a hospital, the hospital shall",
                "provide the parent or agent with a numbered identification bracelet to link the",
                "parent or agent to the infant, unless due to birth of the infant, the infant and parent",
                "already have an identification bracelet. Possession of an identification",
                "bracelet does not entitle the bracelet holder to take custody of the infant . If",
            ],
        ),
        (
            [
                "5.6. If an abandoned infant is left in a newborn safety device at an approved location, the"
            ],
            [
                "6. If an abandoned infant is left in a newborn safety device at an approved location, the"
            ],
        ),
    ],
    "nd_hb1084_introduced_p1.pdf": [
        (
            [
                "3. Act as the state agency to cooperate with the United States department of agriculture",
                "to provide federal-state grading service for poultry and poultry products offered for sale",
                "at the retail level, to supervise the federal-state poultry grading service, and to enforce",
                "regulations at the retail level as to identification by grade of all poultry sold.",
                "4.3. Promote the welfare and improvement of the poultry industry and the marketing of",
            ],
            [
                "3. Promote the welfare and improvement of the poultry industry and the marketing of"
            ],
        ),
        (
            [
                "5.4. Adopt rules under chapter 28-32 as necessary to effectuate the purposes of this"
            ],
            [
                "4. Adopt rules under chapter 28-32 as necessary to effectuate the purposes of this"
            ],
        ),
    ],
    "nd_hb1194_engrossment1_p1.pdf": [
        (
            [
                "A person",
                "1. An individual is guilty of a class A misdemeanor if that personindividual:",
                "1. a. Gives false information or a false report to a law enforcement officer which that",
                "personindividual knows to be false, and the information or report may interfere",
            ],
            [
                "1. An individual is guilty of a class A misdemeanor if that individual:",
                "a. Gives false information or a false report to a law enforcement officer which that",
                "individual knows to be false, and the information or report may interfere",
            ],
        ),
        (
            [
                "2. b. Falsely reports to a law enforcement officer or other security official the"
            ],
            [
                "b. Falsely reports to a law enforcement officer or other security official the"
            ],
        ),
        (
            [
                "response when that personindividual knows that the incident did not occur.",
                '"Security official" means a public servant responsible for averting or dealing',
                "withhandling emergencies involving public safety.",
            ],
            [
                "response when that individual knows the incident did not occur.",
                '"Security official" means a public servant responsible for averting or',
                "handling emergencies involving public safety.",
            ],
        ),
    ],
    "nd_sb2159_enrollment_p1.pdf": [
        (
            [
                "4. TheIf approved by the industrial commission and conducted in consultation with the high-level",
                "radioactive waste advisory council, the state energy research center may not conduct",
            ],
            [
                "4. If approved by the industrial commission and conducted in consultation with the high-level",
                "radioactive waste advisory council, the state energy research center may conduct",
            ],
        )
    ],
}


@pytest.mark.parametrize("name", sorted(ND_EXPECTED_CHANGES))
def test_north_dakota_exactly_the_struck_words_are_taken_out(name):
    data = _read(name)
    old = _lines(extract_sometimes_numbered_pdf(data, META))
    new = _lines(_pdf("nd")(data, META))
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    changes = [
        (old[i1:i2], new[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
    assert changes == ND_EXPECTED_CHANGES[name]
    # Over the whole page, characters are only taken out, never added (spacing aside).
    rest = iter("".join(old).replace(" ", ""))
    assert all(ch in rest for ch in "".join(new).replace(" ", ""))


def test_north_dakota_hb1011_reads_the_new_fee_only():
    data = _read("nd_hb1011_engrossment1_p2.pdf")
    old = "\n".join(_lines(extract_sometimes_numbered_pdf(data, META)))
    new = "\n".join(_lines(_pdf("nd")(data, META)))
    assert "For each broker-dealer $200.00$240" in old  # the defect the ticket reports
    assert "For each broker-dealer $240" in new
    assert "$200.00" not in new


def test_a_north_dakota_page_with_underlines_only_is_read_exactly_as_before():
    # North Dakota underlines new words at 0.85 of the line height, outside the strike band.
    data = _read("nd_hb1011_engrossment1_p1.pdf")
    assert without_struck_text(data) is data
    assert _pdf("nd")(data, META) == extract_sometimes_numbered_pdf(data, META)


def test_a_nevada_page_with_nothing_struck_is_read_exactly_as_before():
    data = _read("nv_ab4_r1_p2.pdf")
    assert without_struck_text(data) is data
    assert _pdf("nv")(data, META) == extract_sometimes_numbered_pdf(data, META)


def test_a_file_pymupdf_cannot_open_is_an_extraction_error():
    # As for Alabama: no quiet fallback that would bring the struck words back.
    for state in ("ga", "la", "nc", "nd", "ne", "nv", "wa"):
        with pytest.raises(Exception):
            _pdf(state)(b"%PDF-1.7\n" + b"\x00" * 64, META)


def test_only_these_eight_states_read_pdfs_without_struck_text():
    # Each of the five keeps its own text extractor underneath, and no other state's
    # reader or media type changes.
    # (Washington's is wrapped twice: OPEN-580 takes the emptied "((" "))" marks out.)
    # Oregon's is wrapped too, but by its bracket reader (OPEN-600, test_or_bracketed.py): it
    # prints no strike line.
    wrapped = {
        state: inspect.unwrap(funcs["application/pdf"])
        for state, funcs in CONVERSION_FUNCTIONS.items()
        if hasattr(funcs.get("application/pdf"), "__wrapped__") and state != "or"
    }
    assert wrapped == {
        "ga": extract_sometimes_numbered_pdf,
        "la": extract_sometimes_numbered_pdf,
        "me": extract_sometimes_numbered_pdf,  # OPEN-871
        "nc": extract_sometimes_numbered_pdf,
        "nd": extract_sometimes_numbered_pdf,
        "ne": extract_sometimes_numbered_pdf,
        "nv": extract_sometimes_numbered_pdf,
        "wa": extract_line_numbered_pdf,
    }
    assert CONVERSION_FUNCTIONS["al"]["application/pdf"] is handle_alabama_pdf
    assert set(CONVERSION_FUNCTIONS["ga"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["la"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["nc"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["nd"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["ne"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["nv"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["wa"]) == {"text/html", "application/pdf"}
