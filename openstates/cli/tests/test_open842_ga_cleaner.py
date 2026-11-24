"""OPEN-842: Georgia's pre-diff cleaner drops the per-page Legislative Counsel number line.

Georgia prints "LC 47 4409S" alone at the top of every page, and the number changes between versions, so every page
break was a removed and an added line in a comparison. The fixtures in fixtures/open842_ga are whole real documents
from the Mac replica (Georgia 2026_ss), one per printed shape, the two SB 3 floor amendments that cite an LC number
inside a sentence, and HB 83's stored "LC 47 4409S/hs" comparison as made before this change.
"""

import pathlib
import re

import pytest

from openstates.cli.text_extract import _compare_texts, apply_prediff_cleaning
from openstates.ddp_states.ga import _clean_ga_text

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "open842_ga"

# The header line as Georgia prints it, written out independently of the cleaner's pattern: the five shapes seen in
# the 284 stored documents.
_HEADER_SHAPES = {
    "hb83_lc_62_0547_a.txt": "LC 62 0547",
    "hb83_lc_47_4409s_hs.txt": "LC 47 4409S",
    "sr4_as_introduced_lc_47_4395_ec.txt": "LC 47 4395-EC",
    "hb7_lc_47_4355_a.txt": "LC LC 47 4355",
    "hr33_lc_112_4337_a.txt": "LC 112 4337",
}


def _read(name):
    return (FIXTURES / name).read_text()


def _changed_lines(diff):
    return [line for line in diff.split("\n") if line[:1] in "+-" and line[:3] not in ("+++", "---")]


@pytest.mark.parametrize("name,header", sorted(_HEADER_SHAPES.items()))
def test_header_lines_go_and_nothing_else_changes(name, header):
    raw = _read(name)
    lines = raw.split("\n")
    assert lines.count(header) >= 2  # the first page and at least one page break
    # The cleaned document is the original minus exactly those lines, every other line byte for byte.
    assert _clean_ga_text(raw) == "\n".join(line for line in lines if line != header)


@pytest.mark.parametrize("name", ["sb3_floor_amend_1.txt", "sb3_floor_amend_2.txt"])
def test_lc_number_cited_inside_a_sentence_stays(name):
    raw = _read(name)
    assert "Amend SB 3EX (LC 47 4392) by " in raw
    assert _clean_ga_text(raw) == raw


@pytest.mark.parametrize(
    "line",
    [
        "Amend SB 3EX (LC 47 4392) by inserting after",
        "(LC 47 4392)",
        "LC 47 4392 by inserting",
        "See LC 47 4392",
        "LC 47",
        "LC 4392",
        "AM 47 0219",
        "LC 47 4392S/hs",
        "  LC 47 4409S",  # matched as printed: Georgia's header has no space around it
        "LC 47 4409S ",
        "\tLC 47 4409S",
    ],
)
def test_close_shapes_are_kept(line):
    text = f"SECTION 1.\n{line}\nSECTION 2."
    assert _clean_ga_text(text) == text


def test_hb83_comparison_loses_only_its_header_lines():
    """The stored comparison (made before this change) minus its 10 header lines is exactly the new comparison."""
    stored = _changed_lines(_read("hb83_lc_47_4409s_hs.stored_diff.txt"))
    new = _changed_lines(
        _compare_texts(
            _read("hb83_lc_62_0547_a.txt"),
            _read("hb83_lc_47_4409s_hs.txt"),
            jurisdiction_name="Georgia",
            is_bill=True,
            prior_media_type="application/pdf",
            cur_media_type="application/pdf",
        )
    )
    header = [line for line in stored if line in ("-LC 62 0547", "+LC 47 4409S")]
    assert len(stored) == 23 and len(header) == 10
    assert new == [line for line in stored if line not in header]


@pytest.mark.parametrize("jurisdiction", ["Florida", "Utah", "Texas"])
def test_other_states_keep_the_line(jurisdiction):
    prior, raw = _read("hb83_lc_62_0547_a.txt"), _read("hb83_lc_47_4409s_hs.txt")
    assert apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name=jurisdiction,
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    ) == (prior, raw)


def test_georgia_cleans_both_sides():
    prior, raw = apply_prediff_cleaning(
        _read("hb83_lc_62_0547_a.txt"),
        _read("hb83_lc_47_4409s_hs.txt"),
        jurisdiction_name="Georgia",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )
    for text in (prior, raw):
        assert not [line for line in text.split("\n") if re.fullmatch(r"LC \d+ \d+S?", line)]
        assert "SECTION 1." in text.split("\n")
