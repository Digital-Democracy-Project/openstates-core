import typing
import re
import tempfile
import textract  # type: ignore

from .utils import (
    pdfdata_to_text,
    text_after_line_numbers,
    text_before_line_numbers,
    text_from_element_lxml,
    text_from_element_xpath,
    text_from_element_siblings_lxml,
    text_from_element_siblings_xpath,
    clean,
)


class Metadata(typing.TypedDict):
    url: str
    media_type: str
    title: str
    jurisdiction_id: str


ExtractorFunc = typing.Callable[[bytes, Metadata], str]


def extract_simple_pdf(data: bytes, metadata: Metadata) -> str:
    return pdfdata_to_text(data)


def extract_line_numbered_pdf(data: bytes, metadata: Metadata) -> str:
    return text_after_line_numbers(pdfdata_to_text(data))


def extract_line_post_numbered_pdf(data: bytes, metadata: Metadata) -> str:
    return text_before_line_numbers(pdfdata_to_text(data))


def extract_sometimes_numbered_pdf(data: bytes, metadata: Metadata) -> str:
    """
    A few states have bills both with numbered lines and without.
    In these cases, we need to look at the start of the lines
    to determine which extraction function to use.
    """

    pdf_text = pdfdata_to_text(data)
    lines = pdf_text.split("\n")

    # Looking for lines that begin with a number
    pattern = re.compile(r"^\s*\d+\s+(.*)", flags=re.MULTILINE)
    number_of_numbered_lines = pattern.findall(pdf_text)

    # If more than 10% of the text begins with numbers, then we are
    # probably looking at a bill with numbered lines.
    THRESHOLD_NUMBERED_PDF = 0.10

    ratio_of_numbered_lines = len(number_of_numbered_lines) / len(lines)

    if ratio_of_numbered_lines > THRESHOLD_NUMBERED_PDF:
        return extract_line_numbered_pdf(data, metadata)
    else:
        return extract_simple_pdf(data, metadata)


def extract_pre_tag_html(data: bytes, metadata: Metadata) -> str:
    """
    Many states that provide bill text on HTML webpages (e.g. AK, FL)
    have the text inside <pre> tags (for preformatted text).
    """

    text_inside_matching_tag = text_from_element_lxml(data, ".//pre")
    return text_after_line_numbers(text_inside_matching_tag)


def extract_from_p_tags_html(data: bytes, metadata: Metadata) -> str:
    """
    For a few states providing bill text in HTML, we just want to get all
    the text in paragraph tags on the page. There may be several paragraphs.
    """

    text = text_from_element_siblings_lxml(data, ".//p")
    return text


def extractor_for_elements_by_class(bill_text_element_class: str) -> ExtractorFunc:
    return extractor_for_element_by_selector(
        ".//div[@class='" + bill_text_element_class + "']"
    )


def extractor_for_element_by_id(bill_text_element_id: str) -> ExtractorFunc:
    return extractor_for_element_by_selector(
        ".//div[@id='" + bill_text_element_id + "']"
    )


def extractor_for_element_by_selector(bill_text_element_selector: str) -> ExtractorFunc:
    def _my_extractor(data: bytes, metadata: Metadata) -> str:
        text_inside_matching_tag = text_from_element_lxml(
            data, bill_text_element_selector
        )
        return clean(text_inside_matching_tag)

    return _my_extractor


def extractor_for_element_by_xpath(bill_text_element_selector: str) -> ExtractorFunc:
    def _my_extractor(data: bytes, metadata: Metadata) -> str:
        text_inside_matching_tag = text_from_element_xpath(
            data, bill_text_element_selector
        )
        return clean(text_inside_matching_tag)

    return _my_extractor


def extractor_for_elements_by_xpath(bill_text_element_selector: str) -> ExtractorFunc:
    def _my_extractor(data: bytes, metadata: Metadata) -> str:
        text_inside_matching_tag = text_from_element_siblings_xpath(
            data, bill_text_element_selector
        )
        return clean(text_inside_matching_tag)

    return _my_extractor


def textract_extractor(**kwargs: str) -> ExtractorFunc:
    """ pass through kwargs to textextract.process """
    assert "extension" in kwargs, "Must supply extension"

    def func(data: bytes, metadata: Metadata) -> str:
        with tempfile.NamedTemporaryFile(delete=False) as tmpf:
            tmpf.write(data)
            tmpf.flush()
            return textract.process(tmpf.name, **kwargs).decode()

    return func


def extract_from_code_tags_html(data: bytes, metadata: Metadata) -> str:
    """
    Some states (e.g. IL) have the bill text inside
    <code> tags (as it renders as fixed-width).
    """

    text = text_from_element_siblings_lxml(data, ".//code")
    return text


# OPEN-210: XML bill documents (UT's `<leg>` export, US govinfo's bill.dtd/USLM) used to be
# extracted by pulling every text node via `itertext()` and joining with single spaces. That
# parses the document correctly and then discards the parse: the result is one enormous line.
#
# That was an accepted trade-off while `raw_text` only fed search and display. It stopped being
# one once archive_bill_versions() started diffing raw_text with `difflib.unified_diff`, which
# is LINE-based -- a single-line document can only ever produce "replace the whole thing".
# Measured before this fix: all 2,074 Utah XML diffs and 6,683 of 6,692 US XML diffs were
# degenerate whole-document hunks, and every PDF diff computed after XML became the preferred
# diff baseline was ruined too (OPEN-209).
#
# The fix is to use the tree lxml already handed us, rather than a hand-maintained list of
# "block-level" tag names per schema -- those rot, and neither of these formats is stable
# enough across stages (bill.dtd vs USLM) to enumerate confidently. Instead block-vs-inline is
# read from the document's own MIXED CONTENT: an element that sits in a run of real text is
# inline by definition, because the source put it there. Everything else starts its own line.
#
# Concretely this keeps `<bold>81-4-502</bold>, as enacted by Laws of Utah 2024` on one line
# (the `<bold>` has a non-empty tail, so it is inline), while `<text>`, `<section>`, `<heading>`
# and friends each get their own line (their surrounding text is whitespace-only).


def _is_inline(el: typing.Any) -> bool:
    """True when `el` sits inside a run of real text and so belongs on its neighbours' line.

    Read from the document rather than from a tag list: an element is inline if there is
    non-whitespace text immediately before it (its parent's own text when it is the first
    child, otherwise the previous sibling's tail) or immediately after it (its own tail).
    """
    parent = el.getparent()
    if parent is None:
        return False
    previous = el.getprevious()
    before = previous.tail if previous is not None else parent.text
    if before and before.strip():
        return True
    return bool(el.tail and el.tail.strip())


def xml_tree_to_lines(root: typing.Any) -> str:
    """Serialize an lxml tree to text, one line per block-level element.

    Inline elements (see `_is_inline`) are folded into the surrounding line so a sentence
    broken up by markup stays a single line -- which is what makes the output stable enough
    for a line-based diff to align two versions of the same bill.
    """
    lines: typing.List[str] = []
    buffer: typing.List[str] = []

    def flush() -> None:
        if buffer:
            # Join the fragments RAW and collapse whitespace once, rather than stripping each
            # fragment and joining with spaces. Inline markup butts directly against its
            # neighbours in the source -- `<bold>81-4-502</bold>, as enacted by ...` -- and
            # per-fragment stripping would render that "81-4-502 , as enacted by", inventing a
            # space before the comma. Harmless-looking, but it is text every downstream reader
            # sees, LegBot included.
            line = " ".join("".join(buffer).split())
            if line:
                lines.append(line)
            buffer.clear()

    def add(text: typing.Optional[str]) -> None:
        if text:
            buffer.append(text)

    def walk(el: typing.Any, within_inline: bool = False) -> None:
        # `within_inline` stops a nested wrapper from breaking a sentence apart:
        # `<p>foo <bold><italic>bar</italic></bold> baz</p>` would otherwise yield "foo" /
        # "bar" / "baz", because <italic> has no adjacent text of its own. Raised by
        # /pm-review.
        #
        # Inheritance is deliberately narrower than "inline elements have inline children",
        # which over-merges: in Utah's `<sa>Utah Code Sections Affected:<saamd><snhead>AMENDS:
        # </snhead>...</saamd></sa>` the <saamd> is adjacent to text and so inline by the rule
        # above, but it is a container of blocks, and inheriting into it swallowed the whole
        # section onto one line.
        #
        # The discriminator is the element's OWN tail, and it is measured rather than guessed.
        # Across the sampled UT/US corpus, 34 elements were inline-with-children whose tail
        # carried real text -- `<quote>` mid-sentence, genuinely embedded in a run -- and 18
        # were inline-with-children whose tail did not -- `<hl>`, a block container that merely
        # follows text. Text after the element means the run continues past it, so its subtree
        # is part of that run; no trailing text means it was appended after a line, not woven
        # into one.
        #
        # The tail check governs ENTERING a run, not continuing one: once inside, an
        # intermediate wrapper with no tail of its own (the <y> in `a <x><y><z>b</z></y></x> c`)
        # must not end it.
        inline = within_inline or _is_inline(el)
        carries_run_onward = bool(el.tail and el.tail.strip())
        if not inline:
            flush()
        add(el.text)
        for child in el:
            if isinstance(child.tag, str):  # skip comments/processing instructions
                walk(child, within_inline=within_inline or (inline and carries_run_onward))
            else:
                add(child.tail)  # a comment's tail is still real document text
        if not inline:
            flush()
        add(el.tail)

    walk(root)
    flush()
    return "\n".join(lines)
