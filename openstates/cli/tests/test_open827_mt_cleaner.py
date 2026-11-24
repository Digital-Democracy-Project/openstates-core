"""OPEN-827: Montana's pre-diff cleaner drops the header's bill-number and sponsor lines and collapses runs of spaces.

The texts are real, from the Mac replica (2026-11-24, 2027 session): SB 30 whole (its Bill Draft and Introduced texts,
a real wording change between them), and the opening lines of SB 25 (the sponsor list wraps onto a second line) and
SJ 3 (a joint resolution).
"""

import pytest

from openstates.cli.text_extract import _compare_texts, apply_prediff_cleaning
from openstates.ddp_states import state_rule_for_name
from openstates.ddp_states.mt import _clean_montana_text, clean_text

SB30_DRAFT = (
    "_____________ BILL NO. _____________\n"
    "INTRODUCED BY _________________________________________________\n"
    "A BILL FOR AN ACT ENTITLED: “AN ACT REQUIRING THE SUICIDE PREVENTION LIFELINE PHONE\n"
    "NUMBER TO BE PRINTED ON STUDENT IDENTIFICATION CARDS; AND PROVIDING AN EFFECTIVE\n"
    "DATE AND AN APPLICABILITY DATE.”\n"
    "BE IT ENACTED BY THE LEGISLATURE OF THE STATE OF MONTANA:\n"
    "NEW SECTION. Section 1.            Suicide prevention 988 lifeline on school identification cards. A\n"
    "school district that issues identification cards to students in any grades 7 through 12 must include the 988\n"
    "suicide and crisis lifeline telephone number on the front or back of student identification cards.\n"
    "NEW SECTION. Section 2.            Codification instruction. [Section 1] is intended to be codified as an\n"
    "integral part of Title 20, chapter 7, part 13, and the provisions of Title 20, chapter 7, part 13, apply to "
    "[section 1].\n"
    "NEW SECTION. Section 3.            Effective date. [This act] is effective July 1, 2027.\n"
    "NEW SECTION. Section 4.            Applicability. [This act] applies to student identification cards printed\n"
    "after [the effective date of this act]. A school district is not required to reprint school identification "
    "cards printed\n"
    "before [the effective date of this act].\n"
    "- END -"
)
SB30_INTRODUCED = (
    "SENATE BILL NO. 30\n"
    "INTRODUCED BY M. YAKAWICH\n"
    "A BILL FOR AN ACT ENTITLED: “AN ACT REQUIRING THE SUICIDE PREVENTION LIFELINE TELEPHONE\n"
    "NUMBER TO BE PRINTED ON STUDENT IDENTIFICATION CARDS; AND PROVIDING AN EFFECTIVE\n"
    "DATE AND AN APPLICABILITY DATE.”\n"
    "BE IT ENACTED BY THE LEGISLATURE OF THE STATE OF MONTANA:\n"
    "NEW SECTION. Section 1.           Suicide prevention 988 lifeline on school identification cards. A\n"
    "school district that issues identification cards to students in any grades 7 through 12 shall include the 988\n"
    "suicide and crisis lifeline telephone number on the front or back of student identification cards.\n"
    "NEW SECTION. Section 2.           Codification instruction. [Section 1] is intended to be codified as an\n"
    "integral part of Title 20, chapter 7, part 13, and the provisions of Title 20, chapter 7, part 13, apply to "
    "[section 1].\n"
    "NEW SECTION. Section 3.           Effective date. [This act] is effective July 1, 2027.\n"
    "NEW SECTION. Section 4.           Applicability. [This act] applies to student identification cards printed on\n"
    "or after [the effective date of this act]. A school district is not required to reprint school identification "
    "cards\n"
    "printed before [the effective date of this act].\n"
    "- END -"
)

SB25_DRAFT_TOP = (
    "_____________ BILL NO. _____________\n"
    "INTRODUCED BY _________________________________________________\n"
    "BY REQUEST OF THE CHILDREN, FAMILIES, HEALTH AND HUMAN SERVICES INTERIM COMMITTEE\n"
    "A BILL FOR AN ACT ENTITLED: “AN ACT CREATING THE STATE RECREATIONAL MARIJUANA IMPACT\n"
    "REPORT; AND ESTABLISHING REPORTING REQUIREMENTS.”"
)
SB25_INTRODUCED_TOP = (
    "SENATE BILL NO. 25\n"
    "INTRODUCED BY M. YAKAWICH, D. LENZ, E. KERR-CARPENTER, J. ETCHART, S. ZINN, G.\n"
    "NIKOLAKAKOS\n"
    "BY REQUEST OF THE CHILDREN, FAMILIES, HEALTH AND HUMAN SERVICES INTERIM COMMITTEE\n"
    "A BILL FOR AN ACT ENTITLED: “AN ACT CREATING THE STATE RECREATIONAL MARIJUANA IMPACT\n"
    "REPORT; AND ESTABLISHING REPORTING REQUIREMENTS.”"
)
SJ3_DRAFT_TOP = (
    "_____________ JOINT RESOLUTION NO. _____________\n"
    "INTRODUCED BY _________________________________________________\n"
    "BY REQUEST OF THE REVENUE INTERIM COMMITTEE\n"
    "A JOINT RESOLUTION OF THE SENATE AND THE HOUSE OF REPRESENTATIVES OF THE STATE OF"
)
SJ3_INTRODUCED_TOP = (
    "SENATE JOINT RESOLUTION NO. 3\n"
    "INTRODUCED BY B. BEARD\n"
    "BY REQUEST OF THE REVENUE INTERIM COMMITTEE\n"
    "A JOINT RESOLUTION OF THE SENATE AND THE HOUSE OF REPRESENTATIVES OF THE STATE OF"
)


def _compare(prior, raw, jurisdiction="Montana"):
    return _compare_texts(
        prior,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


def _changed(diff):
    return [line for line in diff.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))]


@pytest.mark.parametrize(
    "draft,introduced,body",
    [
        (SB25_DRAFT_TOP, SB25_INTRODUCED_TOP, 3),
        (SJ3_DRAFT_TOP, SJ3_INTRODUCED_TOP, 2),
        (SB30_DRAFT, SB30_INTRODUCED, 14),
    ],
)
def test_both_header_lines_are_dropped_in_both_versions(draft, introduced, body):
    for text in (draft, introduced):
        cleaned = _clean_montana_text(text).split("\n")
        assert len(cleaned) == body
        assert not any("BILL NO." in line or "RESOLUTION NO." in line or "INTRODUCED BY" in line for line in cleaned)
        assert cleaned[0].startswith(("BY REQUEST OF ", "A BILL FOR AN ACT", "A JOINT RESOLUTION"))


def test_a_header_only_comparison_becomes_empty():
    assert _changed(_compare(SB25_DRAFT_TOP, SB25_INTRODUCED_TOP, "Nowhere")) != []
    assert _compare(SB25_DRAFT_TOP, SB25_INTRODUCED_TOP) == ""
    assert _compare(SJ3_DRAFT_TOP, SJ3_INTRODUCED_TOP) == ""


def test_the_real_changes_stay_and_the_noise_goes():
    before = _changed(_compare(SB30_DRAFT, SB30_INTRODUCED, "Nowhere"))
    after = _changed(_compare(SB30_DRAFT, SB30_INTRODUCED))
    assert len(before) == 20
    # 20 changed lines -> 10: the header pair and the re-spaced "Section 1." line go, every reworded line stays
    assert after == [
        "-A BILL FOR AN ACT ENTITLED: “AN ACT REQUIRING THE SUICIDE PREVENTION LIFELINE PHONE",
        "+A BILL FOR AN ACT ENTITLED: “AN ACT REQUIRING THE SUICIDE PREVENTION LIFELINE TELEPHONE",
        "-school district that issues identification cards to students in any grades 7 through 12 must include the 988",
        "+school district that issues identification cards to students in any grades 7 through 12 shall include the 988",
        "-NEW SECTION. Section 4. Applicability. [This act] applies to student identification cards printed",
        "-after [the effective date of this act]. A school district is not required to reprint school identification "
        "cards printed",
        "-before [the effective date of this act].",
        "+NEW SECTION. Section 4. Applicability. [This act] applies to student identification cards printed on",
        "+or after [the effective date of this act]. A school district is not required to reprint school identification "
        "cards",
        "+printed before [the effective date of this act].",
    ]


def test_only_header_lines_and_spaces_are_removed():
    for text in (SB30_DRAFT, SB30_INTRODUCED, SB25_INTRODUCED_TOP, SJ3_DRAFT_TOP):
        lines, cleaned = text.split("\n"), _clean_montana_text(text).split("\n")
        cut = len(lines) - len(cleaned)
        assert cut in (2, 3)
        assert ["".join(line.split()) for line in lines[cut:]] == ["".join(line.split()) for line in cleaned]
        assert all("  " not in line for line in cleaned)


@pytest.mark.parametrize(
    "text",
    [
        # the header lines only count at the very top, as a pair
        "A BILL FOR AN ACT\nSENATE BILL NO. 30\nINTRODUCED BY M. YAKAWICH",
        "SENATE BILL NO. 30\nA BILL FOR AN ACT ENTITLED\nINTRODUCED BY M. YAKAWICH",
        "INTRODUCED BY M. YAKAWICH\nSENATE BILL NO. 30\nA BILL FOR AN ACT",
        # body text that cites a bill number is not a header
        "Section 1. As provided in SENATE BILL NO. 30 of the 2025 session,\nINTRODUCED BY the sponsor",
    ],
)
def test_lines_that_only_look_like_the_header_are_kept(text):
    assert _clean_montana_text(text) == text


def test_a_sponsor_list_with_no_end_marker_loses_only_the_two_header_lines():
    body = [f"line {n}" for n in range(1, 9)] + ["BY REQUEST OF the department, the report is filed."]
    text = "HOUSE BILL NO. 5\nINTRODUCED BY A. B\n" + "\n".join(body)
    assert _clean_montana_text(text).split("\n") == body


def test_letter_case_is_left_alone():
    assert _clean_montana_text("Section 1.   NEW SECTION   Text") == "Section 1. NEW SECTION Text"


def test_the_cleaner_is_montana_only():
    assert state_rule_for_name("Montana", "clean_text") is clean_text
    for jurisdiction in ("Wyoming", "Florida", "Alaska"):
        cleaned = apply_prediff_cleaning(
            SB30_DRAFT,
            SB30_INTRODUCED,
            jurisdiction_name=jurisdiction,
            is_bill=True,
            prior_media_type="application/pdf",
            cur_media_type="application/pdf",
        )
        assert cleaned == (SB30_DRAFT, SB30_INTRODUCED), jurisdiction
