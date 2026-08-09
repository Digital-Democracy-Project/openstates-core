import re
import typing

from lxml import etree

from .common import Metadata


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
    shortcut. Pulling every text node via `itertext()` and joining with single spaces is the
    same aggressive-but-effective approach already used for Washington/Texas's bare HTML
    (`extractor_for_element_by_xpath("//html")`) -- it captures real content correctly (verified
    directly against several real bills) at the cost of losing the original document's visual
    line/paragraph structure, which `raw_text` was never expected to preserve exactly anyway
    (see FL/VA's own line-numbered-PDF extractors, which have the same trade-off).
    """
    fixed = _BAD_ENCODING_DECL.sub(b'encoding="UTF-8"', data, count=1)
    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(fixed, parser=parser)
    if root is None:
        raise ValueError("Utah bill XML did not parse even with the encoding fix + recovery")
    parts: typing.List[str] = [t.strip() for t in root.itertext() if t and t.strip()]
    return " ".join(parts)
