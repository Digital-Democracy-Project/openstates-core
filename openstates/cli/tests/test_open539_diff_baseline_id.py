"""OPEN-539: every stored comparison names the document it was made against (`diff_baseline_id`).

The archiver writes it with each new comparison, `recompute-diff-order` with each comparison it writes, and
`recompute-diff-order <state> --baseline-only` stamps the comparisons stored before the column existed, only where
the recompute reproduces the stored comparison.

The two bills are real, copied from the Mac replica (2026-11-15) with their stored texts and stored comparisons
(fixtures/open539_real_versions.json):
* Utah HB 257 (2026): Introduced and Substitute #1, each as XML and PDF, with "Comparison to Original Bill" (a
  not-a-version paper) between them. Each Substitute #1 document is compared with Introduced's document of its own
  media type, never with the comparison paper.
* Massachusetts H 4323 (194th): the enacted chapter law (HTML) is compared with the bill's PDF (OPEN-223).
"""

import csv
import json
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import (
    recompute_diff_order,
    refresh_extraction,
    remove_stale_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

FIXTURE = Path(__file__).parent / "fixtures" / "open539_real_versions.json"
REAL = {b["state"]: b for b in json.loads(FIXTURE.read_text())}

# What each comparison is against, written out by hand from the bills above (not computed by the code under test):
# (note, media type) of a document -> (note, media type) of its baseline. Every other document has no comparison.
EXPECTED = {
    "Utah": {
        ("Substitute #1", "text/xml"): ("Introduced", "text/xml"),
        ("Substitute #1", "application/pdf"): ("Introduced", "application/pdf"),
    },
    "Massachusetts": {
        ("Chapter Law Text (Enacted)", "text/html"): ("Bill Text", "application/pdf"),
    },
}
STATES = sorted(EXPECTED)


def _real_bill(state, versions=None):
    real = REAL[state]
    bill = _make_bill(jid=real["jid"], jurisdiction_name=state)
    bill.identifier = real["identifier"]
    bill.title = real["title"]
    bill.classification = real["classification"]
    bill.save()
    # created in reverse, so the database's own row order is not the version order
    for v in reversed(real["versions"]):
        if versions is not None and v["note"] not in versions:
            continue
        version = bill.versions.create(note=v["note"], date=v["date"])
        for link in v["links"]:
            version.links.create(url=link["url"], media_type=link["media_type"])
    return bill


def _texts(state):
    return {d["source_url"]: d["raw_text"] for d in REAL[state]["documents"]}


def _stored_rows(bill, state, override=None):
    """The replica's rows for the bill, as stored before this column existed (diff_baseline_id empty)."""
    rows = {}
    for d in REAL[state]["documents"]:
        rows[(d["version_note"], d["media_type"])] = BillVersionDocument.objects.create(
            bill=bill,
            version_note=d["version_note"],
            version_date=d["version_date"],
            source_url=d["source_url"],
            media_type=d["media_type"],
            raw_text=d["raw_text"],
            is_error=d["is_error"],
            is_bill_version=d["is_bill_version"],
            diff_from_previous_version=(override or {}).get(
                (d["version_note"], d["media_type"]), d["diff_from_previous_version"]
            ),
            sha256_hash="0" * 64,
            archive_location="s3://bucket/key",
        )
    return rows


def _by_key(bill):
    return {
        (d.version_note, d.media_type): d for d in BillVersionDocument.objects.filter(bill=bill)
    }


def _assert_expected_baselines(bill, state):
    docs = _by_key(bill)
    for key, doc in docs.items():
        if key in EXPECTED[state]:
            assert doc.diff_from_previous_version is not None, key
            assert doc.diff_baseline_id == docs[EXPECTED[state][key]].id, key
        else:
            assert doc.diff_from_previous_version is None, key
            assert doc.diff_baseline_id is None, key


def _recompute(state, *args):
    real = REAL[state]
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=real["jid"]
    ):
        return CliRunner().invoke(recompute_diff_order, [state, *args])


def _ok(state, *args):
    result = _recompute(state, *args)
    assert result.exit_code == 0, result.output
    return result.output


# ---- the archiver ------------------------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("state", STATES)
def test_archiver_stores_each_new_comparison_with_its_baseline(state):
    bill = _real_bill(state)
    counters = _archive(bill, _texts(state))
    assert counters["archived"] == len(REAL[state]["documents"])
    _assert_expected_baselines(bill, state)
    # the comparisons are the ones stored on the replica, so these are the baselines those were made against
    stored = {(d["version_note"], d["media_type"]): d for d in REAL[state]["documents"]}
    for key, doc in _by_key(bill).items():
        assert doc.diff_from_previous_version == stored[key]["diff_from_previous_version"], key


@pytest.mark.django_db
def test_archiver_stamps_a_comparison_against_an_already_archived_version():
    """The baseline row came from an earlier run (the skip path), not this one."""
    bill = _real_bill("Utah", versions={"Introduced"})
    _archive(bill, _texts("Utah"))
    BillVersionDocument.objects.filter(bill=bill).update(archive_location="s3://bucket/key")  # done: skipped next run
    first_ids = {k: d.id for k, d in _by_key(bill).items()}
    for v in REAL["Utah"]["versions"]:
        if v["note"] != "Introduced":
            version = bill.versions.create(note=v["note"], date=v["date"])
            for link in v["links"]:
                version.links.create(url=link["url"], media_type=link["media_type"])
    counters = _archive(bill, _texts("Utah"))
    assert counters["skipped"] == 2 and counters["archived"] == 3
    docs = _by_key(bill)
    assert {k: d.id for k, d in docs.items() if k[0] == "Introduced"} == first_ids
    assert docs[("Substitute #1", "text/xml")].diff_baseline_id == first_ids[("Introduced", "text/xml")]
    assert (
        docs[("Substitute #1", "application/pdf")].diff_baseline_id
        == first_ids[("Introduced", "application/pdf")]
    )


@pytest.mark.django_db
def test_replacing_a_baseline_row_clears_the_comparisons_that_named_it(tmp_path):
    """A row whose upload never finished is fetched again and replaced (OPEN-263) with a new id. A comparison taken
    against the old row must not keep an id that points nowhere."""
    bill = _real_bill("Utah")
    _archive(bill, _texts("Utah"))  # _upload_and_verify is mocked to None: every row is retryable
    docs = _by_key(bill)
    old_intro_xml = docs[("Introduced", "text/xml")].id
    # everything but Introduced XML counts as uploaded, so only that row is replaced on the next run
    BillVersionDocument.objects.filter(bill=bill).exclude(id=old_intro_xml).update(
        archive_location="s3://bucket/key"
    )
    _archive(bill, _texts("Utah"))
    docs = _by_key(bill)
    assert docs[("Introduced", "text/xml")].id != old_intro_xml
    sub_xml = docs[("Substitute #1", "text/xml")]
    assert sub_xml.diff_from_previous_version is not None  # the comparison stays
    assert sub_xml.diff_baseline_id is None  # but no longer names a row that is gone
    assert docs[("Substitute #1", "application/pdf")].diff_baseline_id == docs[
        ("Introduced", "application/pdf")
    ].id
    assert not BillVersionDocument.objects.filter(diff_baseline_id=old_intro_xml).exists()
    # and the stamping pass names the new row
    out = _ok("Utah", "--baseline-only", "--commit", "--list", str(tmp_path / "a.csv"))
    assert "stamp=1 (set=1 changed=0 cleared=0)" in out
    _assert_expected_baselines(bill, "Utah")


@pytest.mark.django_db
def test_a_replaced_row_that_now_fails_extraction_is_no_baseline():
    """If the replacement of a retryable row extracts to nothing, the old row's text is gone from the table, so no
    later document is compared with it (what `recompute-diff-order` would also see)."""
    bill = _real_bill("Utah", versions={"Introduced"})
    _archive(bill, _texts("Utah"))  # rows without archive_location: retryable
    for v in REAL["Utah"]["versions"]:
        if v["note"] != "Introduced":
            version = bill.versions.create(note=v["note"], date=v["date"])
            for link in v["links"]:
                version.links.create(url=link["url"], media_type=link["media_type"])
    texts = _texts("Utah")
    intro_pdf_url = next(
        d["source_url"]
        for d in REAL["Utah"]["documents"]
        if d["version_note"] == "Introduced" and d["media_type"] == "application/pdf"
    )
    texts[intro_pdf_url] = "   \n"  # extracts to whitespace: is_error
    _archive(bill, texts)
    docs = _by_key(bill)
    assert docs[("Introduced", "application/pdf")].is_error
    sub_pdf = docs[("Substitute #1", "application/pdf")]
    assert sub_pdf.diff_from_previous_version is None
    assert sub_pdf.diff_baseline_id is None
    sub_xml = docs[("Substitute #1", "text/xml")]
    assert sub_xml.diff_baseline_id == docs[("Introduced", "text/xml")].id
    ids = set(BillVersionDocument.objects.values_list("id", flat=True))
    assert all(
        d.diff_baseline_id in ids
        for d in BillVersionDocument.objects.exclude(diff_baseline_id=None)
    )


# ---- recompute-diff-order --baseline-only (the stamping pass) ----------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("state", STATES)
def test_stamping_pass_dry_run_counts_and_writes_nothing(state, tmp_path):
    bill = _real_bill(state)
    rows = _stored_rows(bill, state)
    listing = tmp_path / "plan.csv"
    out = _ok(state, "--baseline-only", "--list", str(listing))
    n = len(EXPECTED[state])
    assert "[DRY RUN] 1 bills checked | diffs untouched (--baseline-only)" in out
    assert (
        f"diff_baseline_id: comparisons={n} stamp={n} (set={n} changed=0 cleared=0) "
        "not_stamped_comparison_would_change=0" in out
    )
    assert all(d.diff_baseline_id is None for d in BillVersionDocument.objects.filter(bill=bill))
    with open(listing) as f:
        listed = list(csv.DictReader(f))
    assert {(r["version_note"], r["media_type"]) for r in listed} == set(EXPECTED[state])
    for r in listed:
        key = (r["version_note"], r["media_type"])
        assert int(r["new_diff_baseline_id"]) == rows[EXPECTED[state][key]].id
        assert (r["baseline_note"], r["baseline_media_type"]) == EXPECTED[state][key]
        assert r["old_diff_baseline_id"] == ""


@pytest.mark.django_db
@pytest.mark.parametrize("state", STATES)
def test_stamping_pass_commit_writes_only_the_baseline(state, tmp_path):
    bill = _real_bill(state)
    _stored_rows(bill, state)
    before = {
        d.id: (d.diff_from_previous_version, d.is_bill_version, d.updated_at, d.raw_text)
        for d in BillVersionDocument.objects.filter(bill=bill)
    }
    listing = tmp_path / "stamped.csv"
    n = len(EXPECTED[state])
    out = _ok(state, "--baseline-only", "--commit", "--expect", str(n), "--list", str(listing))
    assert f"[COMMITTED] diff_baseline_id: comparisons={n} stamp={n} (set={n}" in out
    assert "after: comparisons_without_baseline=0" in out
    _assert_expected_baselines(bill, state)
    after = {
        d.id: (d.diff_from_previous_version, d.is_bill_version, d.updated_at, d.raw_text)
        for d in BillVersionDocument.objects.filter(bill=bill)
    }
    assert after == before  # comparison, flag, text and updated_at untouched
    assert listing.exists() and not Path(f"{listing}.partial").exists()
    # a second run finds nothing to stamp
    out = _ok(state, "--baseline-only")
    assert f"comparisons={n} stamp=0 (set=0 changed=0 cleared=0)" in out


@pytest.mark.django_db
def test_stamping_pass_leaves_out_a_comparison_the_recompute_would_change(tmp_path):
    """A stored comparison the recompute does not reproduce was not made against the recomputed baseline: it is not
    stamped, and counted. A full recompute writes that comparison and its baseline together."""
    bill = _real_bill("Utah")
    _stored_rows(bill, "Utah", {("Substitute #1", "text/xml"): "@@ stale @@"})
    out = _ok("Utah", "--baseline-only")
    assert "comparisons=2 stamp=1 (set=1 changed=0 cleared=0) not_stamped_comparison_would_change=1" in out
    # the plan's pass confirms 0 comparisons change: a commit refuses the state and writes nothing
    listing = tmp_path / "a.csv"
    result = _recompute("Utah", "--baseline-only", "--commit", "--list", str(listing))
    assert result.exit_code != 0
    assert "1 stored comparisons are not what the recompute gives" in result.output
    assert all(d.diff_baseline_id is None for d in BillVersionDocument.objects.filter(bill=bill))
    assert not listing.exists() and not Path(f"{listing}.partial").exists()
    assert _by_key(bill)[("Substitute #1", "text/xml")].diff_from_previous_version == "@@ stale @@"

    out = _ok("Utah", "--commit")  # the full recompute
    assert "unchanged=4 corrected=1 nulled=0" in out
    assert "diff_baseline_id: set=2 changed=0 cleared=0" in out
    _assert_expected_baselines(bill, "Utah")


@pytest.mark.django_db
def test_stamping_pass_corrects_and_clears_a_wrong_stamp(tmp_path):
    bill = _real_bill("Utah")
    rows = _stored_rows(bill, "Utah")
    wrong = rows[("Introduced", "text/xml")].id
    BillVersionDocument.objects.filter(id=rows[("Substitute #1", "application/pdf")].id).update(
        diff_baseline_id=wrong
    )
    BillVersionDocument.objects.filter(id=rows[("Comparison to Original Bill", "application/pdf")].id).update(
        diff_baseline_id=wrong
    )
    out = _ok("Utah", "--baseline-only", "--commit", "--list", str(tmp_path / "a.csv"))
    assert "stamp=3 (set=1 changed=1 cleared=1)" in out
    assert "after: comparisons_without_baseline=0" in out
    _assert_expected_baselines(bill, "Utah")


@pytest.mark.django_db
def test_stamping_pass_expect_mismatch_writes_nothing(tmp_path):
    bill = _real_bill("Utah")
    _stored_rows(bill, "Utah")
    listing = tmp_path / "a.csv"
    result = _recompute("Utah", "--baseline-only", "--commit", "--expect", "3", "--list", str(listing))
    assert result.exit_code != 0
    assert "2 documents would be stamped, not the expected 3; nothing written" in result.output
    assert all(d.diff_baseline_id is None for d in BillVersionDocument.objects.filter(bill=bill))
    assert not listing.exists() and not Path(f"{listing}.partial").exists()


@pytest.mark.parametrize(
    "args, message",
    [
        (["--baseline-only", "--commit"], "--commit needs --list FILE"),
        (["--baseline-only", "--flags-only"], "pick one"),
        (["--list", "x.csv"], "--expect and --list go with --baseline-only"),
        (["--expect", "1"], "--expect and --list go with --baseline-only"),
    ],
)
def test_stamping_pass_refuses(args, message):
    with mock.patch("openstates.cli.text_extract.init_django") as init:
        result = CliRunner().invoke(recompute_diff_order, ["ut", *args])
    assert result.exit_code != 0
    assert message in result.output
    init.assert_not_called()


def test_stamping_pass_never_overwrites_a_list(tmp_path):
    listing = tmp_path / "a.csv"
    listing.write_text("earlier record")
    with mock.patch("openstates.cli.text_extract.init_django") as init:
        result = CliRunner().invoke(
            recompute_diff_order, ["ut", "--baseline-only", "--commit", "--list", str(listing)]
        )
    assert result.exit_code != 0 and "already exists" in result.output
    assert listing.read_text() == "earlier record"
    init.assert_not_called()


# ---- recompute-diff-order (full) and --flags-only ----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("state", STATES)
def test_full_recompute_stamps_every_comparison_it_leaves_stored(state):
    bill = _real_bill(state)
    _stored_rows(bill, state)
    n = len(EXPECTED[state])
    out = _ok(state, "--dry-run")
    assert f"diff_baseline_id: set={n} changed=0 cleared=0" in out
    assert all(d.diff_baseline_id is None for d in BillVersionDocument.objects.filter(bill=bill))
    out = _ok(state, "--commit")
    assert f"diff_baseline_id: set={n} changed=0 cleared=0" in out
    _assert_expected_baselines(bill, state)
    assert "diff_baseline_id: set=0 changed=0 cleared=0" in _ok(state, "--dry-run")


@pytest.mark.django_db
def test_flags_only_leaves_the_baseline_alone():
    bill = _real_bill("Utah")
    _stored_rows(bill, "Utah")
    out = _ok("Utah", "--commit", "--flags-only")
    assert "diff_baseline_id" not in out
    assert all(d.diff_baseline_id is None for d in BillVersionDocument.objects.filter(bill=bill))


# ---- refresh-extraction and remove-stale-documents ---------------------------------------------------------------


@pytest.mark.django_db
def test_refresh_extraction_stamps_the_comparisons_it_rewrites():
    bill = _real_bill("Utah")
    rows = _stored_rows(bill, "Utah")
    sub_xml = rows[("Substitute #1", "text/xml")]

    def fake_reextract(doc):
        text = doc.raw_text + "\nSection 99. New text.\n" if doc.id == sub_xml.id else doc.raw_text
        return {"attempted": True, "new_raw_text": text, "new_is_error": False, "reason": None}

    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=REAL["Utah"]["jid"]
    ), mock.patch("openstates.cli.text_extract._reextract_document", side_effect=fake_reextract):
        result = CliRunner().invoke(refresh_extraction, ["ut", "--commit"])
    assert result.exit_code == 0, result.output
    assert "diffs_corrected=1" in result.output
    _assert_expected_baselines(bill, "Utah")
    assert "+Section 99. New text." in _by_key(bill)[("Substitute #1", "text/xml")].diff_from_previous_version


@pytest.mark.django_db
def test_remove_stale_documents_clears_a_baseline_it_removes(tmp_path):
    """A stale copy that a comparison names is removed; the comparison keeps its text and loses the id (the
    recompute this command asks for afterwards restamps it)."""
    bill = _real_bill("Utah")
    rows = _stored_rows(bill, "Utah")
    intro_xml = rows[("Introduced", "text/xml")]
    stale = BillVersionDocument.objects.create(
        bill=bill,
        version_note="Introduced (old name)",
        version_date="",
        source_url=intro_xml.source_url,
        media_type="text/xml",
        raw_text=intro_xml.raw_text,
        sha256_hash=intro_xml.sha256_hash,
        archive_location="s3://bucket/key",
    )
    sub_xml = rows[("Substitute #1", "text/xml")]
    BillVersionDocument.objects.filter(id=sub_xml.id).update(diff_baseline_id=stale.id)
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=REAL["Utah"]["jid"]
    ):
        result = CliRunner().invoke(
            remove_stale_documents, ["ut", "--commit", "--list", str(tmp_path / "removed.csv")]
        )
    assert result.exit_code == 0, result.output
    assert "removed=1" in result.output
    sub_xml.refresh_from_db()
    assert sub_xml.diff_baseline_id is None
    assert sub_xml.diff_from_previous_version is not None
    out = _ok("Utah", "--baseline-only", "--commit", "--list", str(tmp_path / "stamped.csv"))
    assert "stamp=2 (set=2 changed=0 cleared=0)" in out
    _assert_expected_baselines(bill, "Utah")
