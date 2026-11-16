#!/usr/bin/env python
"""OPEN-539: what `recompute-diff-order <state> --baseline-only` would stamp, and every stamp that differs from the
baseline ddp-sync's `_diff_baseline_index` names today. Read-only: it reads the database in a read-only transaction
and api-v3 over HTTP, and writes two files.

    python scripts/open539_compare_baselines.py <state> --ddp-sync-file PATH --out DIR

* The stamps: `recomputed_diffs_for_documents` (the code the pass runs) over every stored document of the state, so it
  works on a database that does not have the `diff_baseline_id` column yet (every stored value is taken as empty).
* ddp-sync's rule: each bill with a stored comparison is read from api-v3 (`/bills/<id>?include=versions`, the call
  ddp-sync makes), and `_diff_baseline_index` is loaded from ddp-sync's own source file (`--ddp-sync-file`,
  `services/local_openstates_client.py`), not copied.
* For each comparison api-v3 returns where the two differ, both baselines are compared again with the stored text
  ("stored comparison reproduced from it": yes/no), and whether ddp-sync's changelog uses that hop today.

Database: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD (the Mac replica: port 5433, openstates_rds_repl_20260911).
api-v3: API_BASE (default http://localhost:8002) and API_KEY. Output: <out>/<state>.json and
<out>/<state>-differences.csv.
"""

import ast
import collections
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import argparse
import difflib

import psycopg2
import requests

import openstates.cli.text_extract as te
from openstates.utils import abbr_to_jid

_NAMES = {"_text_link", "_diff_baseline_index", "_no_earlier_document_of_type"}


def load_ddp_sync_rule(path):
    """ddp-sync's own functions, taken from its source file by name."""
    ns: dict = {}
    for node in ast.parse(open(path).read()).body:
        if isinstance(node, ast.FunctionDef) and node.name in _NAMES:
            exec(compile(ast.Module([node], []), path, "exec"), ns)
    missing = _NAMES - set(ns)
    if missing:
        raise SystemExit(f"{path}: no {sorted(missing)}")
    return ns["_diff_baseline_index"], ns["_text_link"], ns["_no_earlier_document_of_type"]


def reproduces(cur, base_id, doc_id, jurisdiction_name):
    """"yes" when comparing `base_id`'s stored text with `doc_id`'s (the same cleaning and diff as the archiver) gives
    `doc_id`'s stored comparison, "no" when not, "" without a baseline."""
    if not base_id:
        return ""
    cur.execute(
        "select d.id, d.raw_text, d.media_type, d.diff_from_previous_version, b.classification "
        "from ddp_bill_version_document d join opencivicdata_bill b on b.id=d.bill_id where d.id in (%s, %s)",
        [base_id, doc_id],
    )
    rows = {r[0]: r for r in cur.fetchall()}
    base, doc = rows[base_id], rows[doc_id]
    prior, raw = te.apply_prediff_cleaning(
        base[1], doc[1], jurisdiction_name=jurisdiction_name, is_bill=doc[4] == ["bill"],
        prior_media_type=base[2], cur_media_type=doc[2],
    )
    again = "\n".join(difflib.unified_diff(prior.splitlines(), raw.splitlines(), lineterm=""))
    return "yes" if again == doc[3] else "no"


class Doc:
    __slots__ = (
        "id", "version_note", "version_date", "source_url", "media_type", "raw_text", "is_error",
        "diff_from_previous_version",
    )

    def __init__(self, row):
        (self.id, self.version_note, self.version_date, self.source_url, self.media_type, self.raw_text,
         self.is_error, self.diff_from_previous_version) = row


def main(abbr, ddp_sync_file, out_dir):
    _diff_baseline_index, _text_link, _no_earlier = load_ddp_sync_rule(ddp_sync_file)
    jid = abbr_to_jid(abbr)
    conn = psycopg2.connect(options="-c default_transaction_read_only=on")  # PG* environment variables
    cur = conn.cursor()
    cur.execute(
        "select b.id, b.identifier, b.classification, s.identifier, j.name from opencivicdata_bill b "
        "join opencivicdata_legislativesession s on s.id=b.legislative_session_id "
        "join opencivicdata_jurisdiction j on j.id=s.jurisdiction_id "
        "where j.id=%s and exists (select 1 from ddp_bill_version_document d where d.bill_id=b.id)",
        [jid],
    )
    bills = {r[0]: r[1:] for r in cur.fetchall()}
    jname = next(iter(bills.values()))[3] if bills else None
    current = None
    if jname in te._DATE_ORDERED_JURISDICTIONS:
        current = collections.defaultdict(set)
        cur.execute(
            "select v.bill_id, v.note, v.date from opencivicdata_billversion v join opencivicdata_bill b on b.id=v.bill_id "
            "join opencivicdata_legislativesession s on s.id=b.legislative_session_id where s.jurisdiction_id=%s",
            [jid],
        )
        for b, n, d in cur.fetchall():
            current[b].add((n, d))

    stats = collections.Counter()
    meta = {}  # doc id -> (note, date, media)
    baseline_of = {}  # doc id -> baseline doc id (reproduced comparisons only)
    not_stamped = {}  # doc id -> reason (comparison the recompute does not reproduce)
    bills_with_cmp = set()

    named = conn.cursor(name="docs")
    named.itersize = 500
    named.execute(
        "select d.id, d.version_note, d.version_date, d.source_url, d.media_type, d.raw_text, d.is_error, "
        "d.diff_from_previous_version, d.bill_id from ddp_bill_version_document d "
        "join opencivicdata_bill b on b.id=d.bill_id join opencivicdata_legislativesession s on s.id=b.legislative_session_id "
        "where s.jurisdiction_id=%s order by d.bill_id, d.id",
        [jid],
    )

    def flush(bill_id, docs):
        identifier, classification, _session, name = bills[bill_id]
        result = te.recomputed_diffs_for_documents(
            docs, jurisdiction_name=name, is_bill=classification == ["bill"], bill_identifier=identifier,
            current_versions=(current.get(bill_id, set()) if current is not None else None),
        )
        would_change = {id(d): nd for d, nd in result["changed"]}
        for doc, base in result["baselines"]:
            meta[doc.id] = (doc.version_note, doc.version_date, doc.media_type)
            stored = doc.diff_from_previous_version
            if stored is not None:
                stats["comparisons"] += 1
                bills_with_cmp.add(bill_id)
            if id(doc) in would_change:
                new = would_change[id(doc)]
                stats["would_change"] += 1
                if stored is None:
                    stats["would_change_new_comparison"] += 1
                elif new is None:
                    stats["would_change_nulled"] += 1
                    not_stamped[doc.id] = "recompute would null it"
                else:
                    stats["would_change_corrected"] += 1
                    not_stamped[doc.id] = "recompute would rewrite it"
                continue
            if base is not None:
                stats["stamp"] += 1
                baseline_of[doc.id] = base.id

    cur_bill, docs = None, []
    for row in named:
        if row[8] != cur_bill:
            if docs:
                flush(cur_bill, docs)
            cur_bill, docs = row[8], []
        docs.append(Doc(row[:8]))
    if docs:
        flush(cur_bill, docs)
    named.close()

    # ---- ddp-sync's rule over api-v3's answer
    key = os.environ["API_KEY"]
    api_base = os.environ.get("API_BASE", "http://localhost:8002").rstrip("/")
    session = requests.Session()

    def fetch(bill_id):
        for attempt in range(4):
            try:
                r = session.get(
                    f"{api_base}/bills/{bill_id}", params={"include": "versions"},
                    headers={"X-API-KEY": key}, timeout=120,
                )
                if r.status_code == 200:
                    return bill_id, r.json()
                if r.status_code == 404:
                    return bill_id, None
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        return bill_id, "ERROR"

    diffs = []
    with ThreadPoolExecutor(6) as pool:
        for bill_id, data in pool.map(fetch, sorted(bills_with_cmp)):
            if data == "ERROR" or data is None:
                stats["api_v3_unreadable_bills"] += 1
                continue
            versions = data.get("versions") or []
            identifier, _c, session_id, _n = bills[bill_id]
            for i, v in enumerate(versions):
                if v.get("diff_from_previous_version") is None:
                    continue
                d = v.get("archived_document_id")
                stats["api_v3_comparisons"] += 1
                j = _diff_baseline_index(versions, i)
                theirs = versions[j].get("archived_document_id") if j is not None else None
                if d in not_stamped:
                    mine, reason = None, "not stamped: " + not_stamped[d]
                else:
                    mine, reason = baseline_of.get(d), None
                if mine == theirs and reason is None:
                    stats["same"] += 1
                    continue
                # would ddp-sync's caller use this hop today?
                uses = bool(v.get("diff_from_previous_version")) and j is not None
                if uses:
                    old_link, new_link = _text_link(versions[j]), _text_link(v)
                    om = old_link.get("media_type") if old_link else None
                    nm = new_link.get("media_type") if new_link else None
                    if om and nm and om != nm and not _no_earlier(versions, i, nm):
                        uses = False
                if reason:
                    kind = reason
                elif theirs is None:
                    kind = "ddp-sync names no baseline"
                elif mine is None:
                    kind = "no stamp"
                elif meta.get(mine, (None,))[:2] == meta.get(theirs, (None,))[:2]:
                    kind = "same version, other document"
                else:
                    kind = "other version"
                stats["differ"] += 1
                stats["differ: " + kind] += 1
                if uses:
                    stats["differ_and_ddp_sync_uses_the_hop"] += 1
                stamped_ok, theirs_ok = reproduces(cur, mine, d, jname), reproduces(cur, theirs, d, jname)
                if theirs_ok == "no":
                    stats["differ_and_ddp_sync_baseline_does_not_give_the_stored_comparison"] += 1
                m, t = meta.get(mine, ("", "", "")), meta.get(theirs, ("", "", ""))
                diffs.append([
                    abbr, identifier, session_id, bill_id, v.get("note"), d, meta.get(d, ("", "", ""))[2],
                    mine or "", m[0], m[2], stamped_ok, theirs or "", t[0], t[2], theirs_ok, kind,
                    "yes" if uses else "no",
                ])

    out = os.path.join(out_dir, abbr)
    with open(out + ".json", "w") as f:
        json.dump({"state": abbr, "bills": len(bills), "bills_with_comparison": len(bills_with_cmp), **stats}, f, indent=1)
    with open(out + "-differences.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "state", "bill", "session", "bill_id", "version_note", "doc_id", "media_type",
            "stamped_baseline_id", "stamped_baseline_note", "stamped_baseline_media",
            "stored_comparison_reproduced_from_stamped",
            "ddp_sync_baseline_id", "ddp_sync_baseline_note", "ddp_sync_baseline_media",
            "stored_comparison_reproduced_from_ddp_sync", "kind",
            "ddp_sync_uses_this_hop_today",
        ])
        w.writerows(diffs)
    print(abbr, json.dumps(stats))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("state")
    parser.add_argument("--ddp-sync-file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    main(args.state, args.ddp_sync_file, args.out)
