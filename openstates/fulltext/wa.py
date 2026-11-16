"""OPEN-580: Washington's bill text, read without its struck-out words or their "((" "))" marks.

Washington prints a deletion as struck-out text inside double parentheses. In the HTML
printing the struck text is a `<span style="text-decoration:line-through;">` and the marks are
plain text around it: `two-year ((<span style="...line-through;">or</span>))<span
style="text-decoration:underline;">,</span> four-year`. A deletion that runs over several
paragraphs opens "((" before the first struck span and closes "))" after the last one, in a
later paragraph. Measured on the Mac's archive copy (2026-11-16): 3,677 of 5,818 HTML printings
have struck text, 102,227 struck spans, every one a `span` with an inline line-through style.

The HTML is read as before (OPEN-212's `xml_tree_to_lines` over `//html`), with each struck
element taken out first and a placeholder left where it was. A "((" "))" pair holding nothing
but placeholders and spacing (and leaders, see `_EMPTY_MARKS`) is then taken out too. The
placeholder ties each removed pair to a real strike: "((" also appears in formulas and chemical
names ("2-((4-methylpentan-2-yl)amino)") and those are left alone.

The PDF printing is read without its struck words by Alabama's reader (OPEN-560), which leaves
the printed marks around an empty gap: "Clients age (( )) 20". `without_deletion_marks` takes a
pair with only spacing inside out of that text, so both printings of a version read the same.
"""

import re

from lxml import html

from .common import ExtractorFunc, Metadata
from .utils import HTML_BLOCK_TAGS, clean, xml_tree_to_lines

# Stands in for a removed struck element until its marks are taken out. A private-use
# character: it is in no bill, and lxml accepts it in text.
STRUCK = "\ue000"

_LINE_THROUGH = re.compile(r"text-decoration\s*:[^;]*line-through", re.IGNORECASE)

# A "((" "))" pair with nothing inside but spacing, placeholders and leaders, and the spacing
# either side of it. Leaders (dots, hyphens, dashes) count only beside a placeholder: in a deleted
# appropriation line or a deleted run of paragraphs, the table's dot leaders and some hyphens
# between struck spans are not styled struck themselves ("((<s>$4,000</s> . . . .))").
_EMPTY_MARKS = re.compile(
    r"(?P<lead>[ \t]*)\(\((?P<inside>[\s" + STRUCK + r".\-\u2013\u2014]*)\)\)(?P<trail>[ \t]*)"
)


def _is_struck(el) -> bool:
    return isinstance(el.tag, str) and bool(_LINE_THROUGH.search(el.get("style") or ""))


def _drop_mark(match: "re.Match") -> str:
    inside = match.group("inside")
    if STRUCK not in inside and inside.strip():
        return match.group(0)  # leaders with no strike beside them: not a deletion we can see
    # Keep one space between the words either side ("the ((old)) new" -> "the new"), none at
    # either end of a line or before punctuation ("two-year ((or)), four-year" ->
    # "two-year, four-year").
    text, start, end = match.string, match.start(), match.end()
    preceding = text[start - 1] if start else "\n"
    following = text[end] if end < len(text) else "\n"
    if preceding == "\n" or following == "\n" or following in ".,;:)]}":
        return ""
    return " " if match.group("lead") or match.group("trail") else ""


def drop_deletion_marks(text: str) -> str:
    """`text` without its empty "((" "))" pairs: spacing only inside, or spacing, placeholders
    and leaders with at least one placeholder."""
    return _EMPTY_MARKS.sub(_drop_mark, text)


def handle_washington_html(data: bytes, metadata: Metadata) -> str:
    [root] = html.fromstring(data).xpath("//html")
    struck = [el for el in root.iter() if _is_struck(el)]
    for el in struck:
        if any(_is_struck(parent) for parent in el.iterancestors()):
            continue  # taken out with its struck parent
        el.tail = STRUCK + (el.tail or "")
        el.drop_tree()  # keeps the tail
    text = xml_tree_to_lines(root, block_tags=HTML_BLOCK_TAGS)
    if struck:
        text = drop_deletion_marks(text)
        # A placeholder outside any pair is a strike Washington did not mark: it goes too. Each
        # line that held one is spaced again, and a line left empty (a paragraph that was all
        # struck) is dropped, as xml_tree_to_lines drops empty lines.
        lines = []
        for line in text.split("\n"):
            if STRUCK in line:
                line = " ".join(line.replace(STRUCK, " ").split())
            if line:
                lines.append(line)
        text = "\n".join(lines)
    return clean(text)


def without_deletion_marks(extract: ExtractorFunc) -> ExtractorFunc:
    """`extract`, with the empty "((" "))" pairs it leaves taken out of its text."""

    def read(data: bytes, metadata: Metadata) -> str:
        return drop_deletion_marks(extract(data, metadata))

    read.__wrapped__ = extract  # type: ignore[attr-defined]
    return read
