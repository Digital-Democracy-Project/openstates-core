import difflib
import re

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.ut import handle_utah_xml

UT_METADATA = {
    "url": "https://le.utah.gov/Session/2026/bills/introduced/SB0059.xml",
    "title": "x",
    "jurisdiction_id": "ocd-jurisdiction/country:us/state:ut/government",
    "media_type": "text/xml",
}

# Structurally real fixture, reduced from Utah SB0059 (2026 General Session) fetched
# 2026-08-29. Keeps the parts that matter for extraction: the UTF-16 encoding declaration
# Utah's export gets wrong (OPEN-49), block elements carrying their own `lineno`, and a `<sn>`
# whose sentence is split by an inline `<bold>` -- the case that decides whether inline markup
# is folded into its line or wrongly broken onto its own.
UT_XML = b"""<?xml version="1.0" encoding="UTF-16"?>
<leg xml:space="preserve" billnum="SB0059" sess="2026GS">
<lt numlevel="1" lineno="2"><lthead lineno="3">LONG TITLE</lthead>
<gdhead lineno="4">General Description:</gdhead>
<gd numlevel="1" lineno="5">This bill addresses alimony.</gd>
<sa numlevel="1" lineno="15">Utah Code Sections Affected:<saamd lineno="16"><snhead>AMENDS:</snhead>
<sn num="81-4-502" lineno="17"><bold>81-4-502</bold>, as enacted by Laws of Utah 2024, Chapter 366</sn>
</saamd></sa></lt>
<enact numlevel="1" lineno="20">Be it enacted by the Legislature of the state of Utah:</enact>
<bdy><bsec lineno="21"><section number="81-4-502" lineno="22">
<secline lineno="21">Section 1. Section <bold>81-4-502</bold> is amended to read:</secline>
<subsection lineno="23">(1) A court may consider the tax consequences of alimony.</subsection>
</section></bsec></bdy>
</leg>
"""


def test_registered_for_utah_xml():
    """OPEN-49 regression pin: UT had no text/xml entry at all, so every Utah bill served as
    XML extracted empty with is_error=True."""
    assert CONVERSION_FUNCTIONS["ut"]["text/xml"] is handle_utah_xml


def test_extracts_real_text_through_the_bad_encoding_declaration():
    """OPEN-49: Utah declares encoding="UTF-16" over what are really UTF-8 bytes. Without the
    rewrite, libxml2 honours the declaration and fails on the first real content."""
    text = handle_utah_xml(UT_XML, UT_METADATA)

    assert "This bill addresses alimony." in text
    assert "Be it enacted by the Legislature of the state of Utah:" in text
    assert "A court may consider the tax consequences of alimony." in text


def test_output_has_real_line_structure():
    """OPEN-210, the whole point: this used to return one line for an entire bill, which made
    every archived Utah XML diff a degenerate whole-document hunk."""
    lines = handle_utah_xml(UT_XML, UT_METADATA).split("\n")

    assert len(lines) > 5
    assert "LONG TITLE" in lines
    assert "This bill addresses alimony." in lines
    assert all(line == line.strip() for line in lines)
    assert all(line for line in lines), "no blank lines"


def test_inline_markup_stays_on_its_own_line():
    """The case a naive block-per-element walk gets wrong. `<bold>81-4-502</bold>` sits in a run
    of real text, so splitting on it would strand the section number away from its sentence and
    make the line unstable between versions -- defeating the diff this change exists to fix."""
    lines = handle_utah_xml(UT_XML, UT_METADATA).split("\n")

    assert "81-4-502, as enacted by Laws of Utah 2024, Chapter 366" in lines
    assert "Section 1. Section 81-4-502 is amended to read:" in lines
    assert "81-4-502" not in lines, "inline <bold> was broken onto its own line"


def test_no_content_is_lost_relative_to_a_flat_extraction():
    """Structure is added; content is not. Compared against the pre-OPEN-210 behaviour (every
    text node joined with spaces) ignoring whitespace entirely.

    Whitespace has to be ignored rather than merely normalised, because this deliberately
    differs from the old output in one way: the old pass stripped each text node and rejoined
    with spaces, which rendered `<bold>81-4-502</bold>, as enacted by` as "81-4-502 , as
    enacted by" -- inventing a space before the comma. Comparing with all whitespace removed
    still proves no character of real content was added or dropped, while allowing that
    correction."""
    from lxml import etree

    fixed = re.sub(rb'encoding="UTF-16"', b'encoding="UTF-8"', UT_XML, count=1)
    root = etree.fromstring(fixed, parser=etree.XMLParser(recover=True))
    flat = " ".join(t.strip() for t in root.itertext() if t and t.strip())

    strip_ws = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    assert strip_ws(handle_utah_xml(UT_XML, UT_METADATA)) == strip_ws(flat)


def test_a_version_transition_produces_a_targeted_diff():
    """OPEN-209's acceptance condition, at the unit level: two versions of the same bill must
    diff to specific hunks rather than one whole-document replacement."""
    amended = UT_XML.replace(
        b"(1) A court may consider the tax consequences of alimony.",
        b"(1) A court shall consider the tax consequences of alimony.",
    )

    diff = "\n".join(
        difflib.unified_diff(
            handle_utah_xml(UT_XML, UT_METADATA).splitlines(),
            handle_utah_xml(amended, UT_METADATA).splitlines(),
            lineterm="",
        )
    )
    hunks = re.findall(r"@@[^@]*@@", diff)

    assert len(hunks) == 1
    assert not re.fullmatch(r"@@ -1(,\d+)? \+1(,\d+)? @@", hunks[0]), (
        f"whole-document replacement, the bug this fixes: {hunks[0]}"
    )
    assert "-(1) A court may consider" in diff
    assert "+(1) A court shall consider" in diff
