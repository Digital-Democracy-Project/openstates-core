"""OPEN-496: Louisiana's pre-diff cleaner drops the "Page N of M" footer line.

Louisiana's PDFs keep the full centred footer ("Page 1 of 2", 38 or more leading spaces). When
text is inserted every later page number shifts, so the footers show up as changed lines. The
shapes below were read from the production replica (a random 400 Louisiana bills: 417 footer
lines, every one a whole line of exactly `Page N of M`); the sentences are from SCR 38 and the
"On page N" lines are real amendment wording.
"""

import types

import pytest

from openstates.cli.text_extract import (
    _clean_louisiana_text,
    _clean_massachusetts_text,
    apply_prediff_cleaning,
    recomputed_diffs_for_documents,
)

FOOT = " " * 41 + "Page {n} of {m}"


def _page(body, n, m):
    return body + "\n\n" + FOOT.format(n=n, m=m) + "\n\f"


BODY_1 = (
    "To repeal Joint Rule No. 7 of the Joint Rules of Order of the Senate and House of\n"
    "Representatives, relative to the establishment of the Atchafalaya Basin Program\n"
    "Oversight Committee; to repeal the Atchafalaya Basin Program Oversight Committee."
)
BODY_2 = (
    "BE IT RESOLVED that the Legislature of Louisiana repeals Joint Rule No. 7 of the\n"
    "Joint Rules of Order of the Senate and House of Representatives effective the second\n"
    "Monday in March 2027, if the instrument is adopted."
)


@pytest.mark.parametrize(
    "line",
    [
        " " * 41 + "Page 1 of 2",
        " " * 40 + "Page 2 of 2",
        "Page 3 of 12",
        " " * 38 + "Page 10 of 125  ",
        " " * 41 + "Page 1 of 1\f",
        " " * 41 + "page 1 of 2",
    ],
)
def test_footer_lines_are_dropped(line):
    assert _clean_louisiana_text(f"Before the line.\n{line}\nAfter the line.") == (
        "Before the line.\nAfter the line."
    )


@pytest.mark.parametrize(
    "line",
    [
        # Real text that merely mentions a page: the WHOLE line has to be the footer.
        'On page 10, line 16, change "§1200.19" to "§1200.1"',
        "convictions is attached after page 2 of this",
        "Page 1 of 2 of the record shall be attached.",
        "See Page 1 of 2",
        "Page 1",
        "of 12",  # the Massachusetts/Virginia tail is not a Louisiana footer shape
        "Page one of two",
        "Page 1 of",
    ],
)
def test_real_text_that_resembles_a_footer_is_kept(line):
    assert line in _clean_louisiana_text(f"Before the line.\n{line}\nAfter the line.")


def test_only_footer_lines_are_removed_from_a_real_shaped_document():
    text = _page(BODY_1, 1, 2) + _page(BODY_2, 2, 2)
    cleaned = _clean_louisiana_text(text).split("\n")
    expected = [l for l in text.split("\n") if "Page " not in l]
    assert cleaned == expected
    assert BODY_1.split("\n")[0] in cleaned and BODY_2.split("\n")[-1] in cleaned


def _clean(prior, raw, jurisdiction="Louisiana"):
    return apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


def test_the_cleaner_is_louisiana_only():
    prior, raw = _page(BODY_1, 1, 2), _page(BODY_1, 1, 3)
    for jurisdiction in ("Florida", "Utah", "United States"):
        assert _clean(prior, raw, jurisdiction) == (prior, raw), jurisdiction
    assert _clean(prior, raw)[0] == _clean(prior, raw)[1]


def test_massachusetts_still_drops_only_the_tail():
    text = "Before.\nof 12\n" + FOOT.format(n=1, m=2) + "\nAfter."
    assert (
        _clean_massachusetts_text(text)
        == "Before.\n" + FOOT.format(n=1, m=2) + "\nAfter."
    )


def _diff(prior, raw, jurisdiction):
    docs = [
        types.SimpleNamespace(
            version_note=note,
            version_date="",
            source_url=f"https://x.test/{note}.pdf",
            media_type="application/pdf",
            raw_text=text,
            is_error=False,
            diff_from_previous_version=None,
        )
        for note, text in (("HB1 Original", prior), ("HB1 Engrossed", raw))
    ]
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name=jurisdiction, is_bill=True, bill_identifier="HB 1"
    )
    return dict((d.version_note, diff) for d, diff in result["changed"])[
        "HB1 Engrossed"
    ]


def test_inserted_text_no_longer_shows_shifted_page_numbers_in_the_stored_comparison():
    """The comparison the archiver stores, built by the real function: a two page original, a
    three page reprint with a real edit on page 1. Uncleaned, the footer lines are changes."""
    prior = _page(BODY_1, 1, 2) + _page(BODY_2, 2, 2)
    new_body_1 = BODY_1.replace("to repeal the", "to abolish the")
    raw = (
        _page(new_body_1, 1, 3)
        + _page(BODY_2, 2, 3)
        + _page("Section 2. Effective.", 3, 3)
    )
    before = _diff(prior, raw, "Nowhere")
    after = _diff(prior, raw, "Louisiana")
    assert "Page 1 of 3" in before and "-" + FOOT.format(n=1, m=2) in before
    assert "Page" not in after
    # the real edit is still there, and so is the added section
    assert "+Oversight Committee; to abolish the Atchafalaya" in after
    assert "+Section 2. Effective." in after
    assert len(after.split("\n")) < len(before.split("\n"))


def test_a_page_number_only_difference_leaves_an_empty_comparison():
    assert _diff(_page(BODY_1, 1, 2), _page(BODY_1, 1, 3), "Louisiana") == ""
