"""OPEN-223: Massachusetts' bill-as-filed against law-as-signed comparisons.

Every Massachusetts comparison is the PDF "Bill Text" against the HTML "Chapter Law Text
(Enacted)", two versions with two renderings. Since OPEN-217 the HTML had no predecessor of its
own media type, so it got no comparison at all; before that, 193 of the 291 stored ones had no
unchanged line, because the chapter law carries a title, the enacting clause and an "Approved"
line the bill does not, and the two renderings wrap differently. fixtures/open223_ma holds H 4252's
two real texts (production replica, 2026-11-13): four real edits between filing and signature.
"""

import difflib
import pathlib
import types

import pytest

from openstates.cli.text_extract import (
    _clean_massachusetts_text,
    _diff_baseline,
    apply_prediff_cleaning,
    recomputed_diffs_for_documents,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "open223_ma"
BILL_TEXT = (FIXTURES / "h4252_bill_text.txt").read_text()
CHAPTER_LAW = (FIXTURES / "h4252_chapter_law.txt").read_text()
PDF, HTML = "application/pdf", "text/html"
ENACTING = (
    "Be it enacted by the Senate and House of Representatives in General Court assembled, "
    "and by the authority of the same, as follows:"
)


def _docs(*rows):
    return [
        types.SimpleNamespace(
            version_note=note,
            version_date="",
            source_url=f"https://x.test/{i}",
            media_type=media,
            raw_text=text,
            is_error=False,
            diff_from_previous_version=None,
        )
        for i, (note, media, text) in enumerate(rows)
    ]


def _comparison(jurisdiction, prior=BILL_TEXT, raw=CHAPTER_LAW):
    docs = _docs(("Bill Text", PDF, prior), ("Chapter Law Text (Enacted)", HTML, raw))
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name=jurisdiction, is_bill=True, bill_identifier="H 4252"
    )
    diffs = {d.version_note: diff for d, diff in result["changed"]}
    return diffs.get("Chapter Law Text (Enacted)")


def _lines(diff, sign):
    return [
        ln[1:]
        for ln in diff.split("\n")
        if ln.startswith(sign) and not ln.startswith(sign * 3)
    ]


# --- the comparison exists again (acceptance 6) -------------------------------------


def test_the_chapter_law_is_compared_with_the_bill_as_filed():
    assert _comparison("Massachusetts") is not None


def test_every_other_jurisdiction_keeps_the_same_media_baseline():
    """OPEN-217 is unchanged outside Massachusetts: an HTML document with only a PDF before it
    gets no comparison."""
    for other in ("Washington", "Michigan", "Louisiana", "United States"):
        assert _comparison(other) is None, other


@pytest.mark.parametrize(
    "prior_by_media, media, jurisdiction, expected",
    [
        ({PDF: "pdf"}, HTML, "Massachusetts", ("pdf", PDF)),
        ({PDF: "pdf", HTML: "html"}, HTML, "Massachusetts", ("html", HTML)),
        ({HTML: "html"}, PDF, "Massachusetts", (None, PDF)),  # only HTML falls back
        ({}, HTML, "Massachusetts", (None, HTML)),
        ({PDF: "pdf"}, HTML, "Washington", (None, HTML)),
        ({PDF: "pdf"}, PDF, "Washington", ("pdf", PDF)),
    ],
)
def test_diff_baseline(prior_by_media, media, jurisdiction, expected):
    assert _diff_baseline(prior_by_media, media, jurisdiction) == expected


# --- H 4252 (acceptance 3 and 4) ----------------------------------------------------


def test_h4252_shows_its_four_edits_with_the_unchanged_clauses_around_them():
    diff = _comparison("Massachusetts")
    removed, added = _lines(diff, "-"), _lines(diff, "+")
    context = [ln[1:] for ln in diff.split("\n") if ln.startswith(" ")]
    assert context == [
        "In between reviews,",
        "updates to any exaction may be adjusted,",
        "over time,",
    ]
    assert removed == [
        "Subsection (B) of section 2 of chapter 488 of the acts of 1989 is hereby amended by "
        "striking clause (5) and inserting in place thereof the follow clause:-",
        "(5) The level of any exaction shall be reviewed at least every 10 years and reset as "
        "required based upon the recommendation of the office of community development and "
        "the mayor of said city.",
        "based upon changes in the Consumer Price Index for All Urban Consumers compiled by "
        "the United States Bureau of Labor Statistics.",
    ]
    assert added == [
        "Subsection (B) of section 2 of chapter 488 of the acts of 1989 is hereby amended by "
        "striking out clause (5) and inserting in place thereof the follow clause:-",
        "(5) The level of any exaction shall be reviewed not less than every 10 years and "
        "reset as required based upon the recommendation of the office of community "
        "development and the mayor of the city.",
        "based on changes in the Consumer Price Index for All Urban Consumers compiled by "
        "the United States Bureau of Labor Statistics.",
    ]


def test_h4252_has_no_title_enacting_clause_approved_line_or_page_footer():
    diff = _comparison("Massachusetts")
    assert "AN ACT" not in diff
    assert "Be it enacted" not in diff
    assert "Approved" not in diff
    assert not any(ln.strip().startswith("of 1") for ln in diff.split("\n"))
    assert '"' not in diff


# --- the cleaner's rules, one at a time ----------------------------------------------


def _cross(text):
    """The bill-against-law comparison, a PDF against an HTML."""
    return _clean_massachusetts_text(text, cross_media=True)


def test_title_is_dropped_only_near_the_top_and_only_in_capitals():
    text = "AN ACT RELATIVE TO THE TOWN OF HULL\nSECTION 1. The town may act."
    assert _cross(text) == "SECTION 1.\nThe town may act."
    # the PDF's title can sit after a footer tail and a blank line (H 4149)
    assert _cross("of 3\n\nAN ACT REVISING THE CHARTER\nSECTION 1. Kept.") == (
        "SECTION 1.\nKept."
    )
    # not all capitals: kept
    assert _cross("An Act relative to Hull") == "An Act relative to Hull"
    # the fourth line: kept
    late = "SECTION 1.\nSECTION 2.\nSECTION 3.\nAN ACT RELATIVE TO HULL"
    assert "AN ACT RELATIVE TO HULL" in _cross(late)


def test_enacting_clause_is_dropped_and_text_joined_to_it_is_kept():
    assert _cross(ENACTING + "\nSECTION 1. Kept.") == "SECTION 1.\nKept."
    assert _cross(ENACTING + "     SECTION 1. Kept.") == ("SECTION 1.\nKept.")
    # the clause somewhere other than the start of a line is quoted text, kept
    assert "Be it enacted" in _cross("The words " + ENACTING)


def test_approved_line_is_dropped_only_as_a_whole_line():
    for line in ("Approved, November 10, 2025.", "Approved (in part), June 30, 2025."):
        assert _cross(f"SECTION 1. Kept.\n{line}") == "SECTION 1.\nKept."
    for kept in (
        "Approved, as amended, by the town meeting.",
        "Approved, November 10, 2025, by the voters.",
        "The plan approved, November 10, 2025.",
    ):
        assert "pproved" in _cross(kept), kept


def test_page_footer_tail_still_dropped_and_look_alikes_kept():
    for marker in ("of 5", "OF 3", "  of 7  ", "of 5\r"):
        assert _cross(f"one\n{marker}\ntwo") == "one two", marker
    for text in (
        "of the General Laws",
        "of 12 months",
        "total of 12",
        "Page 4 of 12",
        "of 1234",
    ):
        assert _cross(text) == text, text


def test_one_clause_per_line_whatever_the_wrapping():
    pdf = 'by striking clause (5) and inserting the following clause:-\n"(5) The level,\nover time; is\nset. Next: one'
    html = "by striking clause (5) and inserting the following clause:-           (5) The level, over time; is set. Next: one"
    expected = (
        "by striking clause (5) and inserting the following clause:-\n"
        "(5) The level,\nover time;\nis set.\nNext:\none"
    )
    assert _cross(pdf) == expected
    assert _cross(html) == expected
    # a comma inside a number is not a break
    assert _cross("sum of $2,000,000 is") == "sum of $2,000,000 is"


def test_curly_double_quotes_are_removed_and_apostrophes_kept():
    assert _cross("“Eligible adult” means O’Connell's") == (
        "Eligible adult means O’Connell's"
    )


def test_is_dispatched_for_massachusetts_only():
    args = dict(is_bill=True, prior_media_type=PDF, cur_media_type=HTML)
    prior, raw = apply_prediff_cleaning(
        BILL_TEXT, CHAPTER_LAW, jurisdiction_name="Massachusetts", **args
    )
    assert (prior, raw) == (
        _clean_massachusetts_text(BILL_TEXT, cross_media=True),
        _clean_massachusetts_text(CHAPTER_LAW, cross_media=True),
    )
    for other in (
        "Utah",
        "Florida",
        "United States",
        "Georgia",
    ):  # no cleaner of their own
        assert apply_prediff_cleaning(
            BILL_TEXT, CHAPTER_LAW, jurisdiction_name=other, **args
        ) == (BILL_TEXT, CHAPTER_LAW), other


def test_a_page_count_change_alone_is_not_a_diff():
    body = "SECTION 1. The city of Boston may grant\n{marker}\na highway easement.\n"
    prior, raw = apply_prediff_cleaning(
        body.format(marker="of 1"),
        body.format(marker="of 2"),
        jurisdiction_name="Massachusetts",
        is_bill=True,
        prior_media_type=PDF,
        cur_media_type=PDF,
    )
    assert not list(
        difflib.unified_diff(prior.split("\n"), raw.split("\n"), lineterm="")
    )


@pytest.mark.parametrize(
    "prior, raw",
    [
        (
            "AN ACT RELATIVE TO HULL\nSECTION 1. Kept.",
            "AN ACT RELATIVE TO NAHANT\nSECTION 1. Kept.",
        ),
        (
            "SECTION 1. Kept.\nApproved, May 1, 2025.",
            "SECTION 1. Kept.\nApproved, May 2, 2025.",
        ),
        ('striking out the word "Hull"', "striking out the word Hull"),
    ],
)
def test_two_documents_of_the_same_rendering_keep_title_approved_line_and_quotes(
    prior, raw
):
    """pm-review: those three rules are justified only because each exists in one rendering.
    Between two documents of the same rendering a change to any of them must still show; across
    the two renderings the same pair compares equal."""
    same = apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Massachusetts",
        is_bill=True,
        prior_media_type=PDF,
        cur_media_type=PDF,
    )
    across = apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Massachusetts",
        is_bill=True,
        prior_media_type=PDF,
        cur_media_type=HTML,
    )
    assert same[0] != same[1]
    assert across[0] == across[1]


def test_footer_whitespace_and_clauses_apply_to_the_same_rendering_too():
    text = "SECTION 1. The town,\nof 2\n  may   act.\nApproved, May 1, 2025."
    assert _clean_massachusetts_text(text, cross_media=False) == (
        "SECTION 1.\nThe town,\nmay act.\nApproved,\nMay 1,\n2025."
    )
