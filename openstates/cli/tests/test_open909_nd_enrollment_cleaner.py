"""OPEN-909: North Dakota's Enrollment printing is compared without its page headers and certification pages.

The fixtures in fixtures/open909_nd are real stored North Dakota Enrollment texts (Mac replica, 2026-11-27): HB 1412 and
SB 2032 and HCR 3015 whole, the last page of HB 1540 (vetoed: the Governor's objections and the failed override vote)
and a stretch of SB 2269 whose body holds signature rules of its own.
"""

import pathlib

import pytest

from openstates import fulltext
from openstates.cli.text_extract import apply_prediff_cleaning
from openstates.ddp_states import nd
from openstates.ddp_states.nd import _clean_nd_text

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "open909_nd"
KWARGS = dict(is_bill=True, prior_media_type="application/pdf", cur_media_type="application/pdf")


def _read(name):
    return (FIXTURES / name).read_text()


def _expected_body(raw):
    """The text above the certification pages with the page header lines taken out, found without the cleaner's
    patterns: the body ends at the last line before the first printed signature rule that follows a "- PAGE" line."""
    lines = raw.split("\n")
    header = [i for i, line in enumerate(lines) if "NO." in line and "- PAGE" in line]
    cert = next(i for i in header if next(x for x in lines[i + 1 :] if x.strip()).strip().startswith("____"))
    return "\n".join(line for line in lines[:cert] if line not in {lines[i] for i in header})


@pytest.mark.parametrize("name", ["hb1412_whole.txt", "sb2032_whole.txt", "hcr3015_whole.txt"])
def test_page_headers_and_certification_pages_go_and_the_body_is_byte_for_byte(name):
    raw = _read(name)
    got = _clean_nd_text(raw)
    assert got == _expected_body(raw)
    for boilerplate in ("- PAGE", "____", "Governor", "Filed in this office", "This certifies", "Vote:"):
        assert boilerplate not in got
    # real text is still there: the cover, the title and the enacting clause
    assert "BE IT ENACTED BY THE LEGISLATIVE ASSEMBLY OF NORTH DAKOTA:" in got or name.startswith("hcr")
    assert "Sixty-ninth Legislative Assembly of North Dakota" in got


def test_a_vetoed_measure_loses_the_objections_certificate_and_override_vote_and_keeps_section_3():
    raw = _read("hb1540_end_of_bill_and_veto.txt")
    got = _clean_nd_text(raw)
    assert "SECTION 3. EFFECTIVE DATE. Section 1 of this Act becomes effective on July 1, 2026." in got
    assert "objections of Governor Armstrong" in raw
    for gone in ("objections", "Nays", "Honorable Michael Howe", "Received by the Governor", "H. B. NO. 1540"):
        assert gone not in got
    assert got.rstrip().endswith("on July 1, 2026.")


def test_signature_rules_inside_the_body_are_kept_with_the_page_header_between_them_dropped():
    raw = _read("sb2269_signature_rule_in_body.txt")
    got = _clean_nd_text(raw)
    # a header is dropped, the rules a petition form prints in its body are kept, and the line after the header too
    assert "S. B. NO. 2269 - PAGE 3" not in got
    assert got.split("\n").count("                                 _________________________________________") == 1
    assert "reside at____________________________;" in got
    assert got == "\n".join(line for line in raw.split("\n") if "- PAGE 3" not in line)


@pytest.mark.parametrize(
    "line",
    [
        "H. B. NO. 1540 - PAGE 8 of the bill",  # a header with other words on its line
        "see H. B. NO. 1540 - PAGE 8",
        "H. B. NO. 1540",
        "NO. 1540 - PAGE 8",
        "HOUSE BILL NO. 1540 - PAGE 8",  # the cover prints this name, not the page header's
        "Chief Clerk of the House",  # a title alone on a line, outside a certification page
        "Vote:       Yeas 49           Nays 43             Absent 2",
        "Received by the Governor at ________M.",
        "____________________________",
    ],
)
def test_lines_that_are_not_a_page_header_are_kept_in_the_body(line):
    text = f"SECTION 1. The department shall\n{line}\nreport yearly.\n"
    assert _clean_nd_text(text) == text


def test_header_shapes_found_in_the_stored_text_all_match():
    # one of each shape measured in the 648 stored Enrollments, written out independently of the pattern
    for header in (
        "H. B. NO. 1540 - PAGE 8",
        "S. B. NO. 2032 - PAGE 3",
        "H.C.R. NO. 3015 - PAGE 2",
        "S.C.R. NO. 4005 - PAGE 2",
        "H.M.R. NO. 3027 - PAGE 2",
        "H.   R. NO. 3027 - PAGE 2",
        "\x0c                                         H. B. NO. 1412 - PAGE 2",
    ):
        assert _clean_nd_text(f"one\n{header}\ntwo") == "one\ntwo"


def test_north_dakota_cleans_both_sides_and_other_states_keep_the_text():
    prior, raw = _read("sb2032_whole.txt"), _read("hb1412_whole.txt")
    got = apply_prediff_cleaning(prior, raw, jurisdiction_name="North Dakota", **KWARGS)
    assert got == (_clean_nd_text(prior), _clean_nd_text(raw)) and got != (prior, raw)
    for jurisdiction in ("South Dakota", "Florida", "Utah", "Texas"):
        assert apply_prediff_cleaning(prior, raw, jurisdiction_name=jurisdiction, **KWARGS) == (prior, raw)


def test_the_reader_is_unchanged():
    # OPEN-909 adds a cleaner only: ND's reader entry is still the one OPEN-836 set
    assert set(nd.readers()) == {"application/pdf"}
    assert set(fulltext.CONVERSION_FUNCTIONS["nd"]) == set(nd.readers())


def test_a_body_page_that_starts_with_a_signature_rule_is_not_cut():
    # the reviewer's case: a page header, a signature rule right after it, and real body after the rule
    text = (
        "SECTION 1. Petition.\nI swear\n\x0c   S. B. NO. 2269 - PAGE 3\n\n   ______\\_\n   (signature)\n"
        "SECTION 2. Real body continues.\nSECTION 3. More.\n"
    ).replace("______\\_", "____________________________")
    assert _clean_nd_text(text) == (
        "SECTION 1. Petition.\nI swear\n\n   ____________________________\n   (signature)\n"
        "SECTION 2. Real body continues.\nSECTION 3. More.\n"
    )


def test_real_petition_text_with_a_rule_right_after_a_header_keeps_everything_after_it():
    # real SB 2269 lines: the page header and the circulator's rule, put directly one after the other
    lines = _read("sb2269_signature_rule_in_body.txt").split("\n")
    header = next(x for x in lines if "- PAGE 3" in x)
    rule = next(x for x in lines if x.strip().startswith("____") and set(x.strip()) == {"_"})
    at = lines.index(header)
    text = "\n".join(lines[:at] + [header, "", rule] + lines[at + 1 :])
    got = _clean_nd_text(text)
    assert got == "\n".join(lines[:at] + ["", rule] + lines[at + 1 :])
    assert "recalled who are sponsoring the recall." in got  # the end of the stretch is still there


def test_a_certification_page_with_a_line_of_body_in_it_is_not_cut():
    raw = _read("hb1412_whole.txt")
    lines = raw.split("\n")
    at = next(i for i, x in enumerate(lines) if "Received by the Governor" in x)
    lines.insert(at, "SECTION 9. The department shall report yearly.")
    got = _clean_nd_text("\n".join(lines))
    assert "SECTION 9. The department shall report yearly." in got
    assert "Received by the Governor" in got
