"""OPEN-578: one rule for which document of a version is each media type's baseline, for both diff paths.

`recompute-diff-order` used to walk a version's stored rows by row id, so when a version has two documents of one
media type it kept the last by row id as the next version's baseline, while the archiver (and api-v3's
`preferred_document`) keep the last by source_url. Both paths now walk `_version_document_order`.

The bill is real, copied from the Mac replica (2026-11-16) with its stored texts and stored comparisons
(fixtures/open578_va_hb854.json): Virginia HB 854 (2026). Its "Finance Substitute" has two PDFs and two HTMLs, and the
row ids run against the URLs:

    row 25019  1100768.PDF      row 25021  1100769.HTML
    row 25023  1098528.PDF      row 25025  1098529.HTML

The next version ("Subcommittee #2 Subcommittee Substitute") was compared with 25023 and 25025 (the last by row id);
the rule gives 25019 and 25021 (the last by URL). Its PDF comparison changes. Its HTML comparison does not: both
Finance Substitute HTMLs clean to the same text, so only the baseline id moves.

Finance Substitute and "Finance and Appropriations Substitute" tie on `_version_sort_key`, so which comes first is the
order the versions arrive in (row order for the recompute, `bill.versions.all()` for the archiver). That is not this
rule; the tests keep the replica's version order (first row id) on both paths.
"""

import csv
import json
import random
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.text_extract import (
    _version_document_order,
    recompute_diff_order,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

REAL = json.loads((Path(__file__).parent / "fixtures" / "open578_va_hb854.json").read_text())
FILES = "https://lis.blob.core.windows.net/files/"

# Written out by hand from the bill above (not computed by the code under test): each document whose baseline moves
# -> (the baseline by row id, which the stored comparison was made against; the baseline by URL).
MOVES = {
    FILES + "1100764.PDF": (FILES + "1098528.PDF", FILES + "1100768.PDF"),
    FILES + "1100765.HTML": (FILES + "1098529.HTML", FILES + "1100769.HTML"),
}
CHANGED = {FILES + "1100764.PDF"}  # the one stored comparison whose text changes


def _plain_docs():
    """The replica's rows as plain objects, in the replica's row-id order."""
    return [
        SimpleNamespace(diff_baseline_id=None, **{k: d[k] for k in d if k != "is_bill_version"})
        for d in REAL["documents"]
    ]


def _recompute_plain(docs):
    return recomputed_diffs_for_documents(
        docs,
        jurisdiction_name="Virginia",
        is_bill=True,
        bill_identifier=REAL["identifier"],
    )


def _real_bill():
    """The bill with its current versions, created in the replica's version order (first row id; see the docstring)
    and each version's links in reverse URL order, so neither the rows nor the links come in the rule's order."""
    bill = _make_bill(jid=REAL["jid"], jurisdiction_name="Virginia")
    bill.identifier = REAL["identifier"]
    bill.title = REAL["title"]
    bill.classification = REAL["classification"]
    bill.save()
    first_id = {}
    for d in REAL["documents"]:
        first_id.setdefault(d["version_note"], d["id"])
    for v in sorted(REAL["versions"], key=lambda v: first_id[v["note"]]):
        version = bill.versions.create(note=v["note"], date=v["date"])
        for link in sorted(v["links"], key=lambda ln: ln["url"], reverse=True):
            version.links.create(url=link["url"], media_type=link["media_type"])
    return bill


def _stored_rows(bill):
    """The replica's rows, created in its row-id order, so the database's ids run against the URLs as they do there."""
    return {
        d["source_url"]: BillVersionDocument.objects.create(
            bill=bill,
            version_note=d["version_note"],
            version_date=d["version_date"],
            source_url=d["source_url"],
            media_type=d["media_type"],
            raw_text=d["raw_text"],
            is_error=d["is_error"],
            is_bill_version=d["is_bill_version"],
            diff_from_previous_version=d["diff_from_previous_version"],
            sha256_hash="0" * 64,
            archive_location="s3://bucket/key",
        )
        for d in REAL["documents"]
    }


def _cli(*args):
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=REAL["jid"]
    ):
        result = CliRunner().invoke(recompute_diff_order, ["va", *args])
    assert result.exit_code == 0, result.output
    return result.output


# ---- the rule ----------------------------------------------------------------------------------------------------


def test_the_rule_is_media_type_then_source_url():
    rows = [
        ("application/pdf", FILES + "1100768.PDF"),
        ("text/html", FILES + "1098529.HTML"),
        ("application/pdf", FILES + "1098528.PDF"),
        ("text/html", FILES + "1100769.HTML"),
    ]
    ordered = _version_document_order(rows, lambda r: r[0], lambda r: r[1])
    assert ordered == [
        ("application/pdf", FILES + "1098528.PDF"),
        ("application/pdf", FILES + "1100768.PDF"),
        ("text/html", FILES + "1098529.HTML"),
        ("text/html", FILES + "1100769.HTML"),
    ]


def test_the_replica_rows_run_against_the_urls():
    """The premise of the tests below: on the replica the last Finance Substitute row by id is not the last by URL."""
    ids = {d["source_url"]: d["id"] for d in REAL["documents"]}
    assert ids[FILES + "1100768.PDF"] < ids[FILES + "1098528.PDF"]
    assert ids[FILES + "1100769.HTML"] < ids[FILES + "1098529.HTML"]


# ---- recompute-diff-order's walk ---------------------------------------------------------------------------------


def test_recompute_takes_the_last_document_by_url_as_the_baseline():
    result = _recompute_plain(_plain_docs())
    baseline_url = {
        doc.source_url: (base.source_url if base is not None else None) for doc, base in result["baselines"]
    }
    for url, (_old, new) in MOVES.items():
        assert baseline_url[url] == new
    # only the PDF's comparison text changes; every other stored comparison is reproduced
    assert {doc.source_url for doc, _ in result["changed"]} == CHANGED
    assert all(new_diff is not None for _, new_diff in result["changed"])


def _summary(result):
    """{document url: (baseline url, recomputed comparison)}"""
    new_diff = {doc.source_url: diff for doc, diff in result["changed"]}
    return {
        doc.source_url: (
            base.source_url if base is not None else None,
            new_diff.get(doc.source_url, doc.diff_from_previous_version),
        )
        for doc, base in result["baselines"]
    }


def test_recompute_does_not_depend_on_the_order_a_versions_rows_come_in():
    """Rows shuffled within each version (the versions keep the replica's order; see the docstring)."""
    expected = _summary(_recompute_plain(_plain_docs()))
    rng = random.Random(578)
    for _ in range(5):
        by_version = {}
        for doc in _plain_docs():
            by_version.setdefault(doc.version_note, []).append(doc)
        docs = []
        for group in by_version.values():
            rng.shuffle(group)
            docs.extend(group)
        assert _summary(_recompute_plain(docs)) == expected


@pytest.mark.django_db
def test_archiver_and_recompute_make_the_same_comparisons():
    """The archiver on a fresh copy of the bill and the recompute over the replica's rows give every document the
    same comparison and the same baseline document."""
    archived_bill = _real_bill()
    _archive(archived_bill, {d["source_url"]: d["raw_text"] for d in REAL["documents"]})
    archived = {d.source_url: d for d in BillVersionDocument.objects.filter(bill=archived_bill)}
    assert len(archived) == len(REAL["documents"])
    by_id = {d.id: d.source_url for d in archived.values()}

    result = _recompute_plain(_plain_docs())
    new_diff = {doc.source_url: diff for doc, diff in result["changed"]}
    for doc, base in result["baselines"]:
        mine = archived[doc.source_url]
        assert mine.diff_from_previous_version == new_diff.get(doc.source_url, doc.diff_from_previous_version)
        assert by_id.get(mine.diff_baseline_id) == (base.source_url if base is not None else None)
    for url, (_old, new) in MOVES.items():
        assert by_id[archived[url].diff_baseline_id] == new


# ---- the command: the dry-run list, the commit, then --baseline-only --------------------------------------------


@pytest.mark.django_db
def test_dry_run_lists_each_changed_comparison_with_old_and_new_baseline(tmp_path):
    bill = _real_bill()
    rows = _stored_rows(bill)
    before = {d.id: d.diff_from_previous_version for d in BillVersionDocument.objects.filter(bill=bill)}

    # with the rule, --baseline-only cannot stamp this bill until the full recompute: the PDF is not reproduced
    out = _cli("--baseline-only")
    assert "not_stamped_comparison_would_change=1" in out

    listing = tmp_path / "va.csv"
    out = _cli("--dry-run", "--list", str(listing))
    assert "corrected=1 nulled=0" in out
    assert {d.id: d.diff_from_previous_version for d in BillVersionDocument.objects.filter(bill=bill)} == before
    with open(listing) as f:
        listed = list(csv.DictReader(f))
    by_doc = {r["doc_id"]: r for r in listed}
    # every stored comparison is listed: none had a baseline id yet (stored before OPEN-539)
    assert set(by_doc) == {str(d.id) for d in rows.values() if d.diff_from_previous_version is not None}
    assert {r["old_diff_baseline_id"] for r in listed} == {""}
    assert {d for d, r in by_doc.items() if r["comparison"] != "unchanged"} == {str(rows[u].id) for u in CHANGED}
    pdf = by_doc[str(rows[FILES + "1100764.PDF"].id)]
    assert pdf["comparison"] == "corrected"
    assert pdf["old_comparison_made_against"] == str(rows[FILES + "1098528.PDF"].id)
    assert pdf["new_diff_baseline_id"] == str(rows[FILES + "1100768.PDF"].id)
    assert (pdf["baseline_note"], pdf["baseline_media_type"]) == ("Finance Substitute", "application/pdf")
    html = by_doc[str(rows[FILES + "1100765.HTML"].id)]
    assert html["comparison"] == "unchanged"
    assert html["new_diff_baseline_id"] == str(rows[FILES + "1100769.HTML"].id)
    # its stored comparison is reproduced by both Finance Substitute HTMLs: the old baseline and the new one
    assert set(html["old_comparison_made_against"].split(";")) == {
        str(rows[FILES + "1098529.HTML"].id),
        str(rows[FILES + "1100769.HTML"].id),
    }
    # an unchanged comparison is reproduced by its new baseline
    for r in listed:
        if r["comparison"] == "unchanged":
            assert r["new_diff_baseline_id"] in r["old_comparison_made_against"].split(";")


@pytest.mark.django_db
def test_after_the_commit_baseline_only_has_nothing_to_refuse(tmp_path):
    bill = _real_bill()
    rows = _stored_rows(bill)
    listing = tmp_path / "committed.csv"
    out = _cli("--commit", "--list", str(listing))
    assert "corrected=1 nulled=0" in out
    assert not Path(f"{listing}.partial").exists()
    # the list a commit writes records the values before it, as the dry run's does
    with open(listing) as f:
        pdf = {r["doc_id"]: r for r in csv.DictReader(f)}[str(rows[FILES + "1100764.PDF"].id)]
    assert (pdf["comparison"], pdf["old_comparison_made_against"], pdf["new_diff_baseline_id"]) == (
        "corrected",
        str(rows[FILES + "1098528.PDF"].id),
        str(rows[FILES + "1100768.PDF"].id),
    )
    for url, (_old, new) in MOVES.items():
        rows[url].refresh_from_db()
        assert rows[url].diff_baseline_id == rows[new].id
    out = _cli("--baseline-only")
    assert "stamp=0 (set=0 changed=0 cleared=0) not_stamped_comparison_would_change=0" in out
    out = _cli("--baseline-only", "--commit", "--list", str(tmp_path / "stamped.csv"))
    assert "after: comparisons_without_baseline=0" in out
    assert "corrected=0 nulled=0" in _cli("--dry-run")  # a second full run changes nothing


@pytest.mark.django_db
def test_a_commit_whose_list_cannot_be_written_writes_nothing(tmp_path):
    bill = _real_bill()
    _stored_rows(bill)
    before = {
        d.id: (d.diff_from_previous_version, d.diff_baseline_id) for d in BillVersionDocument.objects.filter(bill=bill)
    }
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=REAL["jid"]
    ):
        result = CliRunner().invoke(recompute_diff_order, ["va", "--commit", "--list", str(tmp_path / "no" / "x.csv")])
    assert result.exit_code != 0
    after = {
        d.id: (d.diff_from_previous_version, d.diff_baseline_id) for d in BillVersionDocument.objects.filter(bill=bill)
    }
    assert after == before
