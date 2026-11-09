import io
import typing
import zipfile

from lxml import etree

from .common import Metadata, xml_tree_to_lines

# OPEN-516: Pennsylvania prints words that a later printing removes struck through (with a "<--"
# change bar in the margin). PDF text extraction loses the strike-through, so the removed words
# stayed in the archived text and LegBot read them as current law. Pennsylvania's "DOC" link for
# every printing is served as application/msword but is really an OpenDocument text file (a zip
# holding content.xml), and there the strike-through is explicit: a style whose
# style:text-properties carries style:text-line-through-style other than "none", applied to whole
# paragraphs and to single spans. Underline marks new language added to existing law and is kept;
# [bracketed] existing-law deletions are plain text in this file too and are kept.
#
# The serializer is the federal one (`xml_tree_to_lines`, OPEN-210); what is Pennsylvania's own is
# only the tree edits below, made before serializing.

ODT_MEDIA_TYPE = "application/vnd.oasis.opendocument.text"

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
}
_STYLE_NAME = "{%s}style-name" % _NS["text"]
_LINE_THROUGH = "{%s}text-line-through-style" % _NS["style"]
_STYLE = "{%s}style" % _NS["style"]
_STYLE_OWN_NAME = "{%s}name" % _NS["style"]
_PARENT_STYLE = "{%s}parent-style-name" % _NS["style"]
_STYLE_FAMILY = "{%s}family" % _NS["style"]
_SPACE_COUNT = "{%s}c" % _NS["text"]

# Elements that stay as they are: paragraphs and headings each become one line, and the
# containers around them. Everything else inside the body is inline formatting (spans, links,
# reference marks, bookmarks) and is unwrapped.
_KEPT_ELEMENTS = frozenset(
    {
        "text",
        "p",
        "h",
        "list",
        "list-item",
        "section",
        "table",
        "table-row",
        "table-cell",
    }
)


def is_opendocument_text(data: bytes) -> bool:
    """True when `data` is an OpenDocument text file, read from its bytes: a zip holding a
    `mimetype` entry that names the OpenDocument text type. The format asks for that entry to
    come first, uncompressed, but Pennsylvania's files put settings.xml first, so the zip's
    directory is read instead of a fixed offset."""
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as odt:
            return odt.read("mimetype").strip() == ODT_MEDIA_TYPE.encode()
    except (zipfile.BadZipFile, KeyError):
        return False


def _drop(el: typing.Any) -> None:
    """Remove `el` and everything in it, keeping the text that follows it."""
    parent = el.getparent()
    previous = el.getprevious()
    if el.tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _unwrap(el: typing.Any) -> None:
    """Replace `el` with its own text and children, in place."""
    parent = el.getparent()
    index = parent.index(el)
    previous = el.getprevious()
    children = list(el)
    leading = el.text or ""
    if children:
        children[-1].tail = (children[-1].tail or "") + (el.tail or "")
    else:
        leading += el.tail or ""
    if previous is not None:
        previous.tail = (previous.tail or "") + leading
    else:
        parent.text = (parent.text or "") + leading
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    parent.remove(el)


# A style name belongs to a family (ODF section 16.2): paragraphs and headings use paragraph
# styles, spans use text styles. Other styled elements (sections, lists) carry no text properties.
_STYLE_FAMILY_OF_ELEMENT = {"p": "paragraph", "h": "paragraph", "span": "text"}

_StyleKey = typing.Tuple[typing.Optional[str], str]


def _line_through_by_style(
    *roots: typing.Any,
) -> typing.Dict[_StyleKey, typing.Optional[bool]]:
    """Each style's strike-through, keyed by (family, name): True (struck), False (explicitly
    not struck) or None (the style does not say). A style that does not say takes its parent
    style's setting (style:parent-style-name, same family), in content.xml or styles.xml."""
    own: typing.Dict[
        _StyleKey, typing.Tuple[typing.Optional[bool], typing.Optional[str]]
    ] = {}
    for root in roots:
        for style in root.iter(_STYLE):
            name = style.get(_STYLE_OWN_NAME)
            if not name:
                continue
            value = None
            for props in style.iterfind("style:text-properties", _NS):
                if props.get(_LINE_THROUGH) is not None:
                    value = props.get(_LINE_THROUGH) != "none"
            own[(style.get(_STYLE_FAMILY), name)] = (value, style.get(_PARENT_STYLE))

    def resolve(key: _StyleKey) -> typing.Optional[bool]:
        seen = set()
        while key in own and key not in seen:
            seen.add(key)
            value, parent = own[key]
            if value is not None:
                return value
            key = (key[0], parent)
        return None

    return {key: resolve(key) for key in own}


def _clear_struck_text(
    el: typing.Any,
    styles: typing.Dict[_StyleKey, typing.Optional[bool]],
    struck: bool,
) -> None:
    """Blank every piece of text that is struck through, keeping the elements themselves.

    An element's own style decides for its text and its children; with no style, or one that
    does not say, it is struck exactly when the element around it is. A child's tail is the
    enclosing element's text, so it follows `el`. Text is blanked rather than whole elements
    dropped because a struck paragraph can hold new, explicitly not-struck words: HB 2534
    PN3968 strikes "(2) Generative artificial intelligence." and inserts "AS FOLLOWS:" inside
    the same paragraph."""
    family = _STYLE_FAMILY_OF_ELEMENT.get(etree.QName(el).localname)
    own = styles.get((family, el.get(_STYLE_NAME) or ""))
    if own is not None:
        struck = own
    if struck:
        el.text = None
    for child in el:
        if isinstance(child.tag, str):
            _clear_struck_text(child, styles, struck)
        if struck:
            child.tail = None


def handle_pennsylvania_odt(data: bytes, metadata: Metadata) -> str:
    """Text of a Pennsylvania OpenDocument printing, one line per paragraph, struck text left out.

    1. Parse content.xml with the same lxml settings as `handle_us_bill_xml`.
    2. Remove every draw:frame. Each change bar is an arrow drawing with an empty paragraph
       inside, placed within the real paragraph, and a corrective reprint's "◄Corrective
       Reprint" margin notes are text boxes anchored mid-word (4 of 36 sampled printings).
    3. Blank all struck-through text (struck paragraphs and struck spans; see
       `_clear_struck_text`).
    4. Turn text:s (with its text:c count), text:tab and text:line-break into spaces.
    5. Unwrap the remaining inline elements. Pennsylvania separates an underlined span from its
       neighbours only with text:s elements, so after step 4 it has nothing but whitespace
       beside it, and `xml_tree_to_lines`' mixed-content rule would put it on a line of its own
       and split the sentence.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as odt:
        content = odt.read("content.xml")
        shared_styles = (
            odt.read("styles.xml") if "styles.xml" in odt.namelist() else None
        )
    parser = etree.XMLParser(
        recover=True, resolve_entities=False, no_network=True, load_dtd=False
    )
    root = etree.fromstring(content, parser=parser)
    body = root.find("office:body/office:text", _NS) if root is not None else None
    if body is None:
        raise ValueError("Pennsylvania OpenDocument has no office:text body")

    # styles.xml first, so a same-named automatic style in content.xml wins.
    style_roots = [root]
    if shared_styles:
        shared_root = etree.fromstring(shared_styles, parser=parser)
        if shared_root is not None:
            style_roots.insert(0, shared_root)

    for frame in list(body.iterfind(".//draw:frame", _NS)):
        _drop(frame)
    _clear_struck_text(body, _line_through_by_style(*style_roots), False)
    for tag in ("s", "tab", "line-break"):
        for el in list(body.iterfind(".//text:%s" % tag, _NS)):
            count = int(el.get(_SPACE_COUNT, "1")) if tag == "s" else 1
            el.tail = " " * count + (el.tail or "")
            _drop(el)
    for el in list(body.iter()):
        if (
            el is not body
            and isinstance(el.tag, str)
            and etree.QName(el).localname not in _KEPT_ELEMENTS
        ):
            _unwrap(el)

    return xml_tree_to_lines(body)
