from lxml import html as lxml_html  # type: ignore

from .common import Metadata
from .utils import clean


def handle_virginia_html(data: bytes, metadata: Metadata) -> str:
    """
    Virginia's real bill-text HTML documents (lis.blob.core.windows.net/files/*.HTML) are a
    bare sequence of <p> tags with no wrapping <html>/<body> and no id or class attribute
    anywhere on the page -- the `id="mainC"` container this extractor used to target
    (extractor_for_element_by_id in common.py) no longer exists at all, confirmed by fetching
    real 2026 Regular Session documents directly (SB56, HB1, HJ1).

    Reimplemented locally instead of reusing the shared extract_from_p_tags_html /
    text_from_element_siblings_lxml helpers because those parse raw bytes directly: with no
    charset declared anywhere in the fragment, lxml's HTML parser falls back to guessing an
    8-bit encoding and mangles every non-ASCII character (confirmed against real documents --
    section signs and em dashes in patron/citation lines came out as mojibake). Decoding as
    UTF-8 before parsing fixes this without touching the shared, bytes-typed helper every
    other jurisdiction's HTML extraction still relies on. See OPEN-15.
    """
    document = lxml_html.fromstring(data.decode("utf8", "ignore"))
    text = ""
    for element in document.findall(".//p"):
        text += element.text_content() + "\n"
    return clean(text)
