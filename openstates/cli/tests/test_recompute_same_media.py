"""OPEN-211: `recompute_bill_diff_order` must diff like-for-like, not across media types.

These use stub objects rather than Django models on purpose. The function reads only plain
attributes off each document, so the diffing logic -- which is what OPEN-211 changes -- can be
pinned without a database, unlike the rest of this module's tests.
"""

import re

from openstates.cli.text_extract import recomputed_diffs_for_documents


class FakeDoc:
    def __init__(self, note, media_type, raw_text, diff=None, is_error=False):
        self.version_note = note
        self.version_date = ""
        self.media_type = media_type
        self.raw_text = raw_text
        self.diff_from_previous_version = diff
        self.is_error = is_error

    def __repr__(self):
        return f"<{self.version_note} {self.media_type}>"


class FakeManager:
    def __init__(self, docs):
        self._docs = docs

    def all(self):
        return self._docs


class FakeBill:
    def __init__(self, docs):
        self.version_documents = FakeManager(docs)


# Two renderings of the same bill. The PDF and XML extractions of one version never match each
# other character for character -- different extractors, different line breaks -- which is
# exactly why comparing across them produces a full rewrite instead of a changelog.
PDF_V1 = "SECTION 1\nA court may consider the tax consequences.\nSECTION 2\nEffective July 1."
PDF_V2 = "SECTION 1\nA court shall consider the tax consequences.\nSECTION 2\nEffective July 1."
XML_V1 = "Section 1\nA court may consider the tax consequences.\nSection 2\nEffective July 1."
XML_V2 = "Section 1\nA court shall consider the tax consequences.\nSection 2\nEffective July 1."


def _by_doc(result):
    return {doc: diff for doc, diff in result["changed"]}


def test_each_media_type_diffs_against_its_own_previous_rendering():
    """The defect this ticket fixes. A single PDF-preferred baseline meant the XML of version 2
    was compared against the PDF of version 1 -- measured on Utah SB 0059 as one hunk of 20,161
    characters, "@@ -1,133 +1,148 @@", a full rewrite rather than a changelog."""
    v1_pdf = FakeDoc("Introduced", "application/pdf", PDF_V1)
    v1_xml = FakeDoc("Introduced", "text/xml", XML_V1)
    v2_pdf = FakeDoc("Enrolled", "application/pdf", PDF_V2)
    v2_xml = FakeDoc("Enrolled", "text/xml", XML_V2)

    changed = _by_doc(recomputed_diffs_for_documents([v1_pdf, v1_xml, v2_pdf, v2_xml]))

    for doc in (v2_pdf, v2_xml):
        diff = changed[doc]
        hunks = re.findall(r"@@[^@]*@@", diff)
        assert len(hunks) == 1, f"{doc}: expected one targeted hunk, got {hunks}"
        assert "-A court may consider the tax consequences." in diff
        assert "+A court shall consider the tax consequences." in diff
        # the giveaway of a cross-media comparison: the unchanged headings differ in case
        # between renderings, so they would show up as changes
        assert "SECTION 2" not in diff or "Section 2" not in diff


def test_a_versions_first_appearance_of_a_media_type_has_no_diff():
    """A rendering with no previous counterpart has nothing to diff against, and must record
    that rather than reaching for a different media type's text."""
    v1_pdf = FakeDoc("Introduced", "application/pdf", PDF_V1)
    v2_pdf = FakeDoc("Enrolled", "application/pdf", PDF_V2)
    v2_xml = FakeDoc("Enrolled", "text/xml", XML_V2)  # XML appears only in version 2

    result = recomputed_diffs_for_documents([v1_pdf, v2_pdf, v2_xml])

    # No diff computed and none stored, so there is nothing to correct: it belongs in
    # "unchanged", not in "changed" with a None. Asserting it this way also pins that the
    # command above will not write a pointless update for it.
    assert v2_xml in result["unchanged"]
    assert v2_xml not in _by_doc(result)


def test_a_missing_rendering_does_not_break_the_lineage():
    """A version that happens to ship PDF-only must not orphan the XML chain: version 3's XML
    should still diff against version 1's, not be treated as brand new."""
    v1_xml = FakeDoc("Introduced", "text/xml", XML_V1)
    v2_pdf = FakeDoc("Substitute #1", "application/pdf", PDF_V1)
    v3_xml = FakeDoc("Enrolled", "text/xml", XML_V2)

    changed = _by_doc(recomputed_diffs_for_documents([v1_xml, v2_pdf, v3_xml]))

    diff = changed[v3_xml]
    assert diff is not None and "+A court shall consider the tax consequences." in diff


def test_unchanged_text_produces_no_recorded_change():
    """Idempotence at the diff level: recomputing a bill whose stored diffs are already correct
    must report them unchanged rather than rewriting them."""
    v1 = FakeDoc("Introduced", "application/pdf", PDF_V1)
    v2 = FakeDoc("Enrolled", "application/pdf", PDF_V2)
    first = _by_doc(recomputed_diffs_for_documents([v1, v2]))
    v2.diff_from_previous_version = first[v2]

    second = recomputed_diffs_for_documents([v1, v2])

    assert second["changed"] == []
    assert v2 in second["unchanged"]


def test_a_media_type_that_disappears_and_returns_keeps_its_lineage():
    """Documented product decision (/pm-review asked for it to be explicit).

    `diff_from_previous_version` on a document means "since the previous version OF THIS
    RENDERING". When a version ships PDF-only, the XML chain is not reset -- version 3's XML
    diffs against version 1's rather than recording nothing. That deliberately spans more than
    one version, and the alternative loses a real comparison entirely.

    Rare in practice: 3,090 of 3,100 Utah XML documents and 44,223 of 44,729 US ones sit on a
    version that also has a PDF, so gaps are the exception."""
    v1_xml = FakeDoc("Introduced", "text/xml", XML_V1)
    v2_pdf_only = FakeDoc("Substitute #1", "application/pdf", PDF_V1)
    v3_xml = FakeDoc("Enrolled", "text/xml", XML_V2)

    changed = _by_doc(recomputed_diffs_for_documents([v1_xml, v2_pdf_only, v3_xml]))

    assert "+A court shall consider the tax consequences." in changed[v3_xml]


def test_an_errored_document_does_not_become_a_baseline():
    """An error row has no usable text, so it must not poison the lineage for the next real
    version of that rendering."""
    v1 = FakeDoc("Introduced", "text/xml", XML_V1)
    v2 = FakeDoc("Substitute #1", "text/xml", "", is_error=True)
    v3 = FakeDoc("Enrolled", "text/xml", XML_V2)

    changed = _by_doc(recomputed_diffs_for_documents([v1, v2, v3]))

    assert "+A court shall consider the tax consequences." in changed[v3]


def test_unknown_stage_versions_are_excluded_from_the_lineage():
    """Pre-existing behaviour, pinned because same-media baselines now flow through the same
    branch: a version whose note matches no known stage has no reliable chronological position,
    so it neither takes a diff nor becomes a baseline."""
    v1 = FakeDoc("Introduced", "text/xml", XML_V1)
    mystery = FakeDoc("???", "text/xml", "totally different text")
    v3 = FakeDoc("Enrolled", "text/xml", XML_V2)

    result = recomputed_diffs_for_documents([v1, mystery, v3])
    changed = _by_doc(result)

    assert mystery not in changed
    assert "+A court shall consider the tax consequences." in changed[v3]
