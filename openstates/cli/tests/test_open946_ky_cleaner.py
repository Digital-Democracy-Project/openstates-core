"""OPEN-946: Kentucky's pre-diff cleaner collapses runs of spaces so a re-spaced line is not a change.

The lines are real, from the Mac replica (2026-11-27, session 2026RS, Introduced against Current/Final): HB 508 (the
"SECTION 1." line, 16 spaces then 15), HB 312 ("(3)    (a)      Except" against "(3)     (a)     Except"), HB 103
("(1)    Administrative" against "(1)     Administrative"), and HJR 110's title, where the Current/Final text really adds
words.
"""

import pytest

from openstates.cli.text_extract import _compare_texts, apply_prediff_cleaning
from openstates.ddp_states import state_rule_for_name
from openstates.ddp_states.ky import _clean_ky_text, clean_text

HB508_OLD = (
    "AN ACT relating to the protection of veterans' benefits.\n"
    "Be it enacted by the General Assembly of the Commonwealth of Kentucky:\n"
    "SECTION 1.                A NEW SECTION OF KRS CHAPTER 40 IS CREATED TO\n"
    "READ AS FOLLOWS:"
)
HB508_NEW = HB508_OLD.replace("1.                A", "1.               A")

HB312_OLD = (
    "(b)     Notwithstanding KRS 237.110(4)(c), otherwise meet the criteria set forth in\n"
    "(3)    (a)      Except as provided in this section, the issuance and regulation of\n"
    "provisional licenses to carry concealed firearms and deadly weapons shall"
)
HB312_NEW = HB312_OLD.replace("(3)    (a)      Except", "(3)     (a)     Except")

HB103_OLD = (
    "The cabinet shall provide public health services, including:\n"
    "(1)    Administrative, consultative, technical, professional, and other services needed to\n"
    "assist local health departments in the effective maintenance and operation of their\n"
    "(2)    Administrative, investigative, and clerical services required by the secretary of the"
)
HB103_NEW = HB103_OLD.replace("(1)    Admin", "(1)     Admin").replace("(2)    Admin", "(2)     Admin")

HJR110_OLD = (
    "A JOINT RESOLUTION directing the Kentucky Office of Homeland Security to\n"
    "conduct investigations measuring the Commonwealth's capabilities and vulnerabilities\n"
    "during a potential conflict in the Indo-Pacific and European regions.\n"
    "WHEREAS, recent events have created a heightened risk of conflict that would"
)
HJR110_NEW = HJR110_OLD.replace("European regions.\n", "European regions and declaring an\nemergency.\n")


def compare(prior, raw):
    return _compare_texts(
        prior,
        raw,
        jurisdiction_name="Kentucky",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


@pytest.mark.parametrize("old, new", [(HB508_OLD, HB508_NEW), (HB312_OLD, HB312_NEW), (HB103_OLD, HB103_NEW)])
def test_a_re_spaced_line_is_no_change(old, new):
    assert old != new
    assert compare(old, new) == ""


def test_the_uncleaned_comparison_did_list_the_re_spaced_lines():
    import difflib

    raw = list(difflib.unified_diff(HB508_OLD.splitlines(), HB508_NEW.splitlines(), lineterm=""))
    assert [line[0] for line in raw if line[0] in "+-" and line[:3] not in ("---", "+++")] == ["-", "+"]


def test_a_real_word_change_is_still_a_change():
    diff = compare(HJR110_OLD, HJR110_NEW)
    assert "-during a potential conflict in the Indo-Pacific and European regions." in diff
    assert "+during a potential conflict in the Indo-Pacific and European regions and declaring an" in diff
    assert "+emergency." in diff


def test_a_real_change_beside_a_re_spaced_line_is_the_only_change():
    old = HB103_OLD + "\nShall be paid."
    new = HB103_NEW + "\nShall be paid in full."
    changed = [line for line in compare(old, new).split("\n") if line[:1] in "+-" and line[:3] not in ("---", "+++")]
    assert changed == ["-Shall be paid.", "+Shall be paid in full."]


def test_only_spacing_is_changed_and_a_deleted_law_bracket_stays():
    text = "  (1)    A  person [may  not]\t do this.  \n\n   \nnext   line"
    cleaned = _clean_ky_text(text)
    assert cleaned == "(1) A person [may not] do this.\n\n\nnext line"
    assert cleaned.split() == text.split()


def test_lines_are_cut_where_the_comparison_cuts_them():
    # a form feed (pdftotext's page break) ends a line for the comparison, so the cleaner must not join its two sides
    assert (
        _clean_ky_text("last line of a page\x0c  first   line of the next")
        == "last line of a page\nfirst line of the next"
    )


def test_the_cleaner_runs_through_the_state_file():
    assert state_rule_for_name("Kentucky", "clean_text") is clean_text
    prior, raw = apply_prediff_cleaning(
        "a   b",
        "a b  ",
        jurisdiction_name="Kentucky",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert (prior, raw) == ("a b", "a b")
    same = apply_prediff_cleaning(
        "a   b",
        "a b",
        jurisdiction_name="Alaska",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    assert same == ("a   b", "a b")
