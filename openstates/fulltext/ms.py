"""OPEN-950: Mississippi's bill text (HTML), read without its struck-out words.

Mississippi prints an amendment as the new text with the old text still in the page, marked as a
deletion: `this<b>&nbsp;*&nbsp;*&nbsp;*</b><s><span style='display:none'> article</span></s>
<u>chapter</u>`. The legislature's pages are Word exports: a struck run is an `<s>` element (a
tracked deletion is a `<del>`), and Word hides its words with `display:none`, so a browser shows
"* * * chapter" while the saved source holds "article" as well. The old reader (upstream's
`text_content()` of the page's single `WordSection1`) kept every word, so the HTML copy of a version
read "fine not exceeding Two Hundred Dollars ($200.00) One Thousand Dollars ($1,000.00)". The PDF copy
never held the struck words.

Measured on the Mac's archive copy (2026-11-27), 10,561 HTML documents: 3,386 hold `<s>` elements
(48,470 outermost), 5 hold `<del>`; the words inside are hidden with `display:none` in all but 1,245
`<s>` elements (631 documents), which are plainly visible strikes; 28 hidden spans in 19 amendment
papers sit outside any `<s>` and are deleted words too. No `<strike>` and no `line-through` style
occurs, but `<strike>` is HTML's other strike element and is read the same way.

Every such element is taken out with its words and the page is read as before, so the only thing the
reader changes is that those words are gone: characters are never added, and the spacing around a
removed run is whatever the page held.
"""

from lxml import html

from .common import Metadata
from .utils import clean

_WORD_SECTION = './/*[@class="WordSection1"]'
_STRUCK_TAGS = frozenset({"s", "strike", "del"})


def _is_struck(el) -> bool:
    """A strike element, or one Word hides (it hides struck words, not layout)."""
    if not isinstance(el.tag, str):
        return False
    style = (el.get("style") or "").replace(" ", "").lower()
    return el.tag in _STRUCK_TAGS or "display:none" in style


def handle_mississippi_html(data: bytes, metadata: Metadata) -> str:
    matching_elements = html.fromstring(data).xpath(_WORD_SECTION)
    # As upstream's helper: exit non-zero if the page's layout is not the one this reads.
    assert len(matching_elements) == 1, f"{len(matching_elements)} matches for {_WORD_SECTION}"
    [section] = matching_elements
    for el in [e for e in section.iter() if _is_struck(e)]:
        if any(_is_struck(parent) for parent in el.iterancestors()):
            continue  # taken out with its struck parent
        el.drop_tree()  # keeps the tail
    return clean(section.text_content())
