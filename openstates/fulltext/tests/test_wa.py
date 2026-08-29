"""OPEN-212: Washington HTML extraction fused tokens across element boundaries."""

import re

from lxml import html

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.common import extractor_for_element_by_xpath
from openstates.fulltext.utils import text_from_element_xpath

WA_METADATA = {
    "url": "http://lawfilesext.leg.wa.gov/Biennium/2025-26/Htm/Bills/"
    "Senate%20Passed%20Legislature/5167-S.PL.htm",
    "title": "x",
    "jurisdiction_id": "ocd-jurisdiction/country:us/state:wa/government",
    "media_type": "text/html",
}

# Shaped exactly like real WA bill HTML: block elements with NO newline or whitespace between
# them, which is what makes lxml's separator-free text_content() fuse their text. Reduced from
# ESSB 5167 (2025 regular session) fetched 2026-08-29 -- the document whose stored raw_text
# recorded the bill number as "516769".
WA_HTML = (
    b"<html><body>"
    b"<p>CERTIFICATION OF ENROLLMENT</p>"
    b"<p>ENGROSSED SUBSTITUTE SENATE BILL 5167</p>"
    b"<p>69TH LEGISLATURE</p>"
    b"<p>2025 REGULAR SESSION</p>"
    b"<p>Sec. 1. Section <b>81-4-502</b> is amended to read:</p>"
    b"</body></html>"
)


def lines_of(doc: bytes) -> list:
    """Serialize through the same path WA's registered extractor uses."""
    return CONVERSION_FUNCTIONS["wa"]["text/html"](doc, WA_METADATA).split("\n")


def test_wa_html_uses_structured_extraction():
    """Regression pin. The bug is invisible from the output of any single element -- it only
    appears where two blocks abut -- so pin the wiring itself."""
    assert CONVERSION_FUNCTIONS["wa"]["text/html"].__name__ == "_my_extractor"
    assert "516769" not in CONVERSION_FUNCTIONS["wa"]["text/html"](WA_HTML, WA_METADATA)


def test_tokens_are_not_fused_across_block_boundaries():
    """The actual defect, stated as the real regression it produced.

    Engrossed Substitute Senate Bill **5167** was stored as bill **516769**, because
    text_content() joined "...BILL 5167" and "69TH LEGISLATURE" with no separator. The bill
    number became unsearchable and any reader saw a number that was never in the document."""
    text = CONVERSION_FUNCTIONS["wa"]["text/html"](WA_HTML, WA_METADATA)

    assert "516769" not in text
    assert "ENROLLMENTENGROSSED" not in text
    assert "LEGISLATURE2025" not in text

    lines = text.split("\n")
    assert "ENGROSSED SUBSTITUTE SENATE BILL 5167" in lines
    assert "69TH LEGISLATURE" in lines


def test_old_behaviour_really_did_fuse_them():
    """Guards the test above from becoming vacuous: if lxml ever changed text_content() to
    insert separators, the assertions above would pass for the wrong reason."""
    fused = html.fromstring(WA_HTML).xpath("//html")[0].text_content()

    assert "516769" in fused
    assert "ENROLLMENTENGROSSED" in fused


def test_inline_markup_still_folds_into_its_sentence():
    lines = CONVERSION_FUNCTIONS["wa"]["text/html"](WA_HTML, WA_METADATA).split("\n")

    assert "Sec. 1. Section 81-4-502 is amended to read:" in lines


def test_content_is_preserved_exactly_apart_from_separators():
    """Structure and separators are added; no character of content is added or lost. Verified
    on the real 4.6MB ESSB 5167 document at 2,610,274 characters either way."""
    fused = html.fromstring(WA_HTML).xpath("//html")[0].text_content()
    structured = CONVERSION_FUNCTIONS["wa"]["text/html"](WA_HTML, WA_METADATA)

    strip_ws = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    assert strip_ws(fused) == strip_ws(structured)


def test_default_path_is_unchanged_for_every_other_jurisdiction():
    """Eight other jurisdictions share this helper and all measure clean today -- zero
    whole-document diffs -- because their sources ship HTML with real newlines. This change
    must not touch them, so the default stays text_content()."""
    assert text_from_element_xpath(WA_HTML, "//html") == (
        html.fromstring(WA_HTML).xpath("//html")[0].text_content()
    )

    legacy = extractor_for_element_by_xpath("//html")
    assert "516769" in legacy(WA_HTML, WA_METADATA), "default path should still fuse"


def test_block_and_inline_elements_adjacent_to_bare_text_are_distinguished():
    """/pm-review's central objection: `<div>foo</div>bar` and `<span>foo</span>bar` have an
    identical text/tail shape, so the mixed-content rule alone cannot tell them apart -- yet
    they need opposite treatment.

    HTML resolves it, because HTML carries display semantics XML does not. `HTML_BLOCK_TAGS` is
    a fixed spec constant, not the per-jurisdiction schema list this serializer refuses to
    maintain: it does not drift per source and cannot rot.

    Rare but real -- 2 of 29,525 block elements across four sampled WA documents, both
    `<div>Secretary</div>Secretary`, which folded to "SecretarySecretary" without this."""
    assert lines_of(b"<html><body><div>foo</div>bar</body></html>") == ["foo", "bar"]
    assert lines_of(b"<html><body><span>foo</span>bar</body></html>") == ["foobar"]
    assert lines_of(b"<html><body><div>Secretary</div>Secretary</body></html>") == [
        "Secretary",
        "Secretary",
    ]


def test_inline_markup_inside_a_word_does_not_split_the_word():
    """The other half of the same objection: inline composition must survive. Splitting here
    would invent two words where the document has one."""
    assert lines_of(b"<html><body><p>Wash<b>ing</b>ton</p></body></html>") == ["Washington"]


def test_adjacent_blocks_with_no_source_whitespace_are_separated():
    assert lines_of(b"<html><body><p>BILL 5167</p><p>69TH LEGISLATURE</p></body></html>") == [
        "BILL 5167",
        "69TH LEGISLATURE",
    ]


def test_line_breaks_and_empty_elements():
    assert lines_of(b"<html><body><p>a<br/>b</p><p></p><p>c</p></body></html>") == ["ab", "c"]


def test_helper_is_importable_from_both_its_old_and_new_locations():
    """It moved from common.py to utils.py so utils could use it; common.py re-exports because
    ut.py/us.py import it from there. Pins that both paths resolve to the same object and that
    the late re-export does not hit partial initialisation."""
    from openstates.fulltext.common import xml_tree_to_lines as from_common
    from openstates.fulltext.utils import xml_tree_to_lines as from_utils

    assert from_common is from_utils
