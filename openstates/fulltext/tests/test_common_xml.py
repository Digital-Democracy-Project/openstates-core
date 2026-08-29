"""Tests for `xml_tree_to_lines`, the shared XML serializer behind the UT/US bill extractors.

The handlers' own tests cover their schemas end to end; these pin the block/inline rule itself,
including the cases /pm-review raised against the first implementation.
"""

import re

from lxml import etree

from openstates.fulltext.common import xml_tree_to_lines


def lines_of(xml: bytes) -> list:
    return xml_tree_to_lines(etree.fromstring(xml)).split("\n")


def test_block_siblings_get_their_own_lines():
    assert lines_of(b"<s><text>one</text><text>two</text></s>") == ["one", "two"]


def test_inline_markup_is_folded_into_its_line():
    assert lines_of(b"<p>foo <bold>bar</bold> baz</p>") == ["foo bar baz"]


def test_inline_adjacency_is_preserved_without_inventing_spaces():
    """The reason fragments are joined raw rather than stripped-and-space-joined."""
    assert lines_of(b"<sn><bold>81-4-502</bold>, as enacted</sn>") == ["81-4-502, as enacted"]


def test_nested_inline_does_not_break_a_sentence():
    """/pm-review, high severity, confirmed against the first implementation: `_is_inline` only
    inspects an element's immediate neighbours, so a wrapper nested inside an inline run had no
    adjacent text of its own and flushed mid-sentence -- turning "foo bar baz" into three
    lines. Inline context is inherited by descendants now."""
    assert lines_of(b"<p>foo <bold><italic>bar</italic></bold> baz</p>") == ["foo bar baz"]
    assert lines_of(b"<p>a <x><y><z>b</z></y></x> c</p>") == ["a b c"]


def test_adjacent_bare_wrappers_stay_separate_lines():
    """Deliberate, and the reason is that nothing structural distinguishes these two cases.

    `<subparagraph><enum>(A)</enum><text>...</text></subparagraph>` (real, 77 of 376 multi-child
    parents in the sampled corpus) is element-for-element the same shape as two consecutive
    `<text>` blocks: leaf children, text in each, no text between them. Merging the first would
    merge the second, joining two distinct provisions onto one line -- a worse error than an
    enumerator sitting on its own line, and one that would also make the line unstable when a
    neighbouring provision is amended.

    Telling them apart requires knowing that `enum` labels `text`, which is schema knowledge,
    and a per-schema tag list is exactly what this serializer avoids. Pinned so the behaviour is
    a decision rather than an accident."""
    assert lines_of(b"<sp><enum>(A)</enum><text>the provision</text></sp>") == [
        "(A)",
        "the provision",
    ]


def test_comment_and_processing_instruction_tails_are_kept():
    """Their tails are ordinary document text; only the nodes themselves are skipped."""
    assert lines_of(b"<p>before <!-- note --> after</p>") == ["before after"]
    assert lines_of(b"<p>before <?pi data?> after</p>") == ["before after"]


def test_empty_and_whitespace_only_elements_produce_no_lines():
    assert lines_of(b"<r><a></a><b>   </b><c>real</c></r>") == ["real"]


def test_no_blank_or_unstripped_lines():
    out = xml_tree_to_lines(etree.fromstring(b"<r>\n  <a> padded </a>\n  <b>x</b>\n</r>"))
    lines = out.split("\n")
    assert lines == ["padded", "x"]
    assert all(line and line == line.strip() for line in lines)


def test_every_text_node_appears_exactly_once():
    """The preservation invariant, stated structurally rather than by comparing against the old
    implementation: each text and tail node in the document must survive into the output exactly
    once -- never dropped, never duplicated by the inline/block bookkeeping."""
    xml = b"""<bill>
      <form><congress>118th CONGRESS</congress><legis-num>S. 507</legis-num></form>
      <legis-body>
        <section><enum>1.</enum><header>Short title</header>
          <text>This Act may be cited as the <quote>Test Act</quote>, and so on.</text>
        </section>
        <section><text>A second <bold><italic>nested</italic></bold> provision.</text></section>
      </legis-body>
    </bill>"""
    root = etree.fromstring(xml)

    expected = [t.strip() for t in root.itertext() if t and t.strip()]
    output = xml_tree_to_lines(root)

    haystack = re.sub(r"\s+", "", output)
    for fragment in expected:
        needle = re.sub(r"\s+", "", fragment)
        assert haystack.count(needle) >= 1, f"lost: {fragment!r}"
    # and nothing invented: identical character content, whitespace aside
    assert haystack == re.sub(r"\s+", "", "".join(expected))


def test_deep_nesting_is_within_recursion_limits():
    """/pm-review asked whether the recursive walk could blow the stack. Real bills are shallow
    -- the deepest element in the sampled UT/US corpus sits 9 ancestors down, against Python's
    default limit of 1000 -- but this pins a depth an order of magnitude past anything observed
    so the answer is a test rather than an assurance."""
    depth = 200
    xml = ("<r>" + "<a>" * depth + "deep" + "</a>" * depth + "</r>").encode()

    assert xml_tree_to_lines(etree.fromstring(xml)) == "deep"
