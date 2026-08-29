import re

from lxml import etree

from .common import Metadata, xml_tree_to_lines


# Utah's own bill XML export declares `encoding="UTF-16"` in its XML prolog, but the actual
# bytes are plain UTF-8/ASCII (confirmed directly: no UTF-16 byte-order-mark, and every byte
# in the prolog itself is single-byte ASCII, e.g. b"<?xml" not b"<\x00?\x00x\x00m\x00l\x00").
# libxml2 honors the declared encoding and chokes almost immediately on real content ("Blank
# needed here" at column ~38, right where the mismatch starts). Rewriting the declaration to
# match the real bytes (found 2026-08-09, OPEN-49) is the whole fix -- the document is
# otherwise well-formed XML.
_BAD_ENCODING_DECL = re.compile(rb'encoding="UTF-16"', re.IGNORECASE)


def handle_utah_xml(data: bytes, metadata: Metadata) -> str:
    """
    Extract readable text from a Utah bill XML document.

    Utah's format (`<leg>` root) carries the bill's actual text spread across many structural
    elements (title/sponsor headers, then numbered section/paragraph elements for the body) --
    unlike a page-oriented PDF/HTML, there's no single "the text is in this one element"
    shortcut.

    OPEN-210: serialized via `xml_tree_to_lines()`, which emits one line per block-level
    element and folds inline markup into its surrounding line. This replaced an
    `itertext()`-and-join-with-spaces pass that produced a single line for the entire bill --
    fine while `raw_text` only fed search and display, but not once it became the input to a
    line-based `difflib` diff (every one of Utah's 2,074 archived XML diffs was a degenerate
    whole-document hunk as a result; see OPEN-209).

    Utah's markup makes the block/inline split unambiguous: `<bold>`/`<xref>` and friends sit
    in mixed content (`<bold>81-4-502</bold>, as enacted by ...`) and stay on their line, while
    `<secline>`, `<sn>`, `<hl>` and the rest each start one.
    """
    fixed = _BAD_ENCODING_DECL.sub(b'encoding="UTF-8"', data, count=1)
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(fixed, parser=parser)
    if root is None:
        raise ValueError("Utah bill XML did not parse even with the encoding fix + recovery")
    return xml_tree_to_lines(root)
