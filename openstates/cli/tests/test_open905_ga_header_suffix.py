"""OPEN-905: Georgia's per-page Legislative Counsel number line is dropped with its version suffix too.

The regular session (2025_26) prints "LC 57 0200/AP" or "LC 61 0483S (SCS)", not only "LC 47 4409S". The fixtures in
fixtures/open905_ga are real stretches of stored 2025_26 documents (Mac replica), one per suffix shape, from the first
page header to the second, so each holds a page break.
"""

import pathlib

import pytest

from openstates.cli.text_extract import apply_prediff_cleaning
from openstates.ddp_states.ga import _clean_ga_text

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "open905_ga"

# The header line as Georgia prints it, written out independently of the cleaner's pattern: every suffix shape found
# in the stored 2025_26 and 2026_ss text.
_HEADER_SHAPES = {
    "ap.txt": "LC 57 0200/AP",
    "sap.txt": "LC 62 0513S/AP",
    "ecsap.txt": "LC 59 0350-ECS/AP",
    "ecap.txt": "LC 48 1688-EC/AP",
    "erap.txt": "LC 44 3113ER/AP",
    "s_scs.txt": "LC 61 0483S (SCS)",
    "s_scs_nospace.txt": "LC 60 0155S(SCS)",
    "ecs_scs.txt": "LC 56 0640-ECS (SCS)",
}


def _read(name):
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize("name,header", sorted(_HEADER_SHAPES.items()))
def test_suffixed_header_lines_go_and_nothing_else_changes(name, header):
    raw = _read(name)
    lines = raw.split("\n")
    assert lines.count(header) == 2  # the first page and one page break
    # The cleaned text is the original minus exactly those lines, every other line byte for byte.
    assert _clean_ga_text(raw) == "\n".join(line for line in lines if line != header)


@pytest.mark.parametrize(
    "line",
    [
        "Amend the Senate substitute to HR 7 (LC 39 4801S) by inserting the following between lines",  # real lines
        "Amend SB 173 (LC 55 0401):",
        "LC 47 4392/AP by inserting",  # a line that begins like a header but goes on
        "LC 47 4392/AP.",
        "LC 47 4392S (SCS) by striking",
        "LC 47 4392S (SCS",
        "LC 47 4392/",
        "LC 47 4392 /AP",
        "LC 47 4392/AP/AP",
        "LC 47 4392S/hs",  # lower case is in a version's name only, never printed on the page
        "LC 47 4392/ap",
        "LC 47 4392/ABC",  # well formed but not a tag Georgia prints: only /AP and (SCS) were found in 37,050 headers
        "LC 47 4392 (NOTE)",
        "LC 47 4392S (SCS)/AP",  # no stored header carries both
        "LC 47 4392 (scs)",
        "LC 47 4392 (SCS)extra",
        "LC 47 4392 (SCS) ",
        " LC 57 0200/AP",
        "LC 57 0200/AP ",
        "Table 1. Lower-Bound Estimated Revenue Effects of Substitute HB 66 LC 50 1265",
    ],
)
def test_lines_that_are_not_a_whole_header_are_kept(line):
    text = f"SECTION 1.\n{line}\nSECTION 2."
    assert _clean_ga_text(text) == text


def test_georgia_cleans_a_suffixed_header_on_both_sides_and_other_states_keep_it():
    prior, raw = _read("ap.txt"), _read("s_scs.txt")
    kwargs = dict(is_bill=True, prior_media_type="application/pdf", cur_media_type="application/pdf")
    got = apply_prediff_cleaning(prior, raw, jurisdiction_name="Georgia", **kwargs)
    assert got == (_clean_ga_text(prior), _clean_ga_text(raw))
    assert got != (prior, raw)
    for jurisdiction in ("Florida", "Utah", "Texas"):
        assert apply_prediff_cleaning(prior, raw, jurisdiction_name=jurisdiction, **kwargs) == (prior, raw)
