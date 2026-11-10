"""OPEN-529: Alabama's pre-diff cleaner also drops the Enrolled signature and certification block,
the front header, and spacing differences inside a line.

Every Enrolled text ends with a signature and certification block (the "____" lines for the
Speaker and the President of the Senate, the Clerk's or Secretary's certificate, dated chamber
actions); the committee substitutes and "Reported Out of Committee" versions print no front header
("HB503 / tracking code / By Representative ... / RFD: ... / First Read: ..."); and the budget
bills re-space the same table lines. The fixtures in fixtures/open529_al are real texts from the
production replica (Alabama 2026): whole documents for the short ones, and for the long ones the
real first 30 and last 40 lines ("_excerpt").
"""

import difflib
import pathlib
import re

import pytest

from openstates.cli.text_extract import _clean_al_text, apply_prediff_cleaning

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "open529_al"

# Lines of the signature and certification block, and of the front header, as printed.
_BLOCK_LINE_RE = re.compile(
    r"_{10,}|Speaker of the House of Representatives|"
    r"President and Presiding Officer of the Senate|I hereby certify that the within Act.*|"
    r"(?:Senate|House)\s+\d{1,2}-[A-Z][a-z]{2}-\d{2}.*|Passed: .*|By: .*|"
    r"(?:RFD|First Read|PFD):.*|(?:HB|SB|HJR|SJR)\d+|John Treadwell|Clerk|Patrick Harris,|"
    r"Secretary\."
)
# Lines that are bill text by any reading: a section, a resolution clause, an enacting clause.
_REAL_LINE_RE = re.compile(r"(?:Section \d|WHEREAS|BE IT|\(a\)|\(1\)).*")


def _read(name):
    return (FIXTURES / name).read_text()


def _norm(line):
    return " ".join(line.split())


def _changed(prior, raw):
    prior, raw = apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Alabama",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    return [
        line
        for line in difflib.unified_diff(
            prior.splitlines(), raw.splitlines(), lineterm=""
        )
        if line[:1] in "+-" and line[:3] not in ("+++", "---")
    ]


@pytest.mark.parametrize(
    "name",
    [
        "hjr271_enrolled.txt",
        "sjr64_enrolled.txt",
        "hb248_enrolled_excerpt.txt",
        "sb22_enrolled_excerpt.txt",
        "sb292_enrolled_excerpt.txt",
        "sb146_enrolled_excerpt.txt",
    ],
)
def test_the_enrolled_signature_block_and_header_are_gone_and_every_real_line_is_kept(
    name,
):
    text = _read(name)
    cleaned = _clean_al_text(text).split("\n")
    assert "I hereby certify that the within Act" in text  # the fixture has the block
    assert not [line for line in cleaned if _BLOCK_LINE_RE.fullmatch(line)]
    real = [_norm(ln) for ln in text.split("\n") if _REAL_LINE_RE.match(_norm(ln))]
    assert real and all(line in cleaned for line in real)


def test_hjr271_engrossed_to_enrolled_is_only_the_reworded_title():
    # Before OPEN-529 this comparison was 23 changed lines, the signature block "added" in all
    # of them but the title: LegBot listed the block as "Sections Added".
    changed = _changed(_read("hjr271_engrossed.txt"), _read("hjr271_enrolled.txt"))
    assert changed == [
        "-HJR271 NAMING THE PORTION OF ALABAMA HIGHWAY 21 FROM MILE",
        "-MARKER 39 TO MILE MARKER 41 IN MONROE COUNTY THE SPECIAL",
        "-AGENT JUSTIN WILLIAMS MEMORIAL HIGHWAY.",
        "+NAMING THE PORTION OF ALABAMA HIGHWAY 21 FROM MILE MARKER 39",
        "+TO MILE MARKER 41 IN MONROE COUNTY THE SPECIAL AGENT JUSTIN",
        "+WILLIAMS MEMORIAL HIGHWAY.",
    ]


def test_sjr64_introduced_to_enrolled_is_empty_when_only_the_wrapper_differs():
    # Before OPEN-529: "-RFD:" / "+RFD: Rules" and the 13-line signature block, 15 changed lines
    # and no change to the resolution; LegBot read "RFD: Rules" as a modified section.
    assert _changed(_read("sjr64_introduced.txt"), _read("sjr64_enrolled.txt")) == []


def test_a_version_printed_without_the_header_does_not_show_it_as_removed():
    # HB 503: "Reported Out of Committee" prints no "HB503 / By Representative Givan / RFD:
    # Judiciary / First Read: 24-Feb-26" header; the one real change is "by law" -> "by law or
    # civil proceedings".
    changed = _changed(_read("hb503_introduced.txt"), _read("hb503_reported.txt"))
    assert changed == ["-by law.", "+by law or civil proceedings."]


def test_re_spaced_budget_table_lines_are_no_change():
    # Real line pairs from SB 146 (General Fund budget), first substitute -> Engrossed.
    prior = (
        "(1) State General Fund                       12,604,076\n"
        "Total Legislative Council                      12,604,076                     "
        "12,604,076\n"
        "(a) Legislative Operations and                4,995,744           280,000    "
        "5,275,744\n"
    )
    raw = (
        "(1) State General Fund                 12,604,076\n"
        "Total Legislative Council                12,604,076                       "
        "12,604,076\n"
        "(a) Legislative Operations and          4,995,744          280,000       "
        "5,275,744\n"
    )
    assert _changed(prior, raw) == []
    # a real change to a figure still shows
    assert _changed(prior, raw.replace("12,604,076\n", "12,604,077\n", 1)) == [
        "-(1) State General Fund 12,604,076",
        "+(1) State General Fund 12,604,077",
    ]


def test_signature_wording_in_the_bill_text_is_kept():
    # SB 22's Enrolled text names the Speaker in its sections; only the exact title line under a
    # "____" line at the end starts the block.
    text = (
        "HB1\nTRK12-1\nBy Representative Smith\nRFD: Rules\nFirst Read: 15-Jan-26\n"
        "Section 1. A member appointed by the\n"
        "Speaker of the House of Representatives.\n"
        "________________________________________________\n"
        "shall sign here.\n"
        "Section 2. This act is effective on October 1, 2026.\n"
    )
    cleaned = _clean_al_text(text)
    assert "Speaker of the House of Representatives." in cleaned
    assert "shall sign here." in cleaned
    assert "Section 2. This act is effective on October 1, 2026." in cleaned


def test_a_signature_line_far_from_the_end_cuts_nothing():
    body = "".join(f"Section {n}. Real text number {n}.\n" for n in range(40))
    text = (
        "Section 0. Start.\n________________________________________________\n"
        f"Speaker of the House of Representatives\n{body}"
    )
    assert "Real text number 39" in _clean_al_text(text)


@pytest.mark.parametrize(
    "text",
    [
        # no bare bill number on the first line: nothing is a header
        "By Representative Smith\nRFD: Rules\nSection 1. Real text.",
        # a bill number but no RFD:/First Read:/PFD: line after it
        "HB1\nBy Representative Smith\nSection 1. Real text.",
        # an "RFD:" line past the header region is not a header marker
        "HB1\n" + "".join(f"Line {n}.\n" for n in range(45)) + "RFD: Rules\nEnd.",
    ],
)
def test_no_header_anchor_means_no_header_cut(text):
    cleaned = _clean_al_text(text)
    assert cleaned.split("\n")[0] == text.split("\n")[0]


def test_the_header_cut_stops_at_the_last_marker():
    text = (
        "SB22\n5VHBF33-1\nBy Senators Givhan, Orr,\nSmith, Jones\nRFD: Rules\n"
        "First Read: 13-Jan-26\nPFD: 31-Oct-25\nRelating to the commission.\n"
    )
    assert _clean_al_text(text) == "Relating to the commission.\n"


def test_the_header_anchor_is_judged_before_other_rules_remove_a_line():
    # "A BILL" is dropped by the title rule; the bill number it sat above is still not line 1 of
    # the document as printed, so nothing is cut as a header.
    cleaned = _clean_al_text("A BILL\nHB1\nRFD: Rules\nSection 1. Real text.")
    assert cleaned == "HB1\nRFD: Rules\nSection 1. Real text."


def test_the_signature_anchor_is_judged_before_other_rules_remove_a_line():
    # "AN ACT" between the "____" line and the title is dropped by the title rule; the two were
    # not adjacent as printed, so the text after them is kept.
    text = (
        "Section 1. Real text.\n________________________________________________\n"
        "AN ACT\nSpeaker of the House of Representatives\nSection 2. More real text."
    )
    cleaned = _clean_al_text(text)
    assert "Speaker of the House of Representatives" in cleaned
    assert "Section 2. More real text." in cleaned


def test_the_older_rules_keep_counting_lines_from_the_top_of_the_document():
    # OPEN-372's tracking-code rule drops line 2 of the document as printed; once the header is
    # cut, the second line of what is left is bill text and stays.
    text = (
        "HB1\nTRK12-1\nBy Representative Smith\nRFD: Rules\nFirst Read: 15-Jan-26\n"
        "Section 1. Real text.\nABC12-3\nSection 2. More real text."
    )
    assert _clean_al_text(text) == (
        "Section 1. Real text.\nABC12-3\nSection 2. More real text."
    )
