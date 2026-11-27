#!/usr/bin/env python
"""OPEN-911: which states' PDF readers keep only margin-numbered lines and lose the rest.

Upstream's `extract_line_numbered_pdf` keeps only lines that start with a margin line number, and
`extract_sometimes_numbered_pdf` switches to it when more than 10% of a PDF's lines are numbered. A paper
(or a line of it) a state prints without margin numbers silently vanishes from the stored text
(Kentucky acts OPEN-906, Missouri OPEN-908). This script measures it for each state on the local archive
copy of the PDFs. Read-only: it opens files and a CSV, prints a report, never touches a database, S3 or
the archive.

    # 1. the rows (a SELECT on the replica; the SQL is printed by --sql)
    python scripts/open911_sweep_numbered_pdf_readers.py --sql ky mo > /tmp/rows.sql
    psql ... -c "\\copy ($(cat /tmp/rows.sql)) to stdout csv" > /tmp/rows.csv
    # 2. the sweep (in an image with the core installed: it reads each state's reader from
    #    openstates.fulltext.CONVERSION_FUNCTIONS, so a fixed reader shows as fixed)
    python scripts/open911_sweep_numbered_pdf_readers.py ky mo --rows /tmp/rows.csv \
        [--root /Volumes/DDP-HOT/bills/raw] [--sample 300] [--details out.jsonl]
    python scripts/open911_sweep_numbered_pdf_readers.py --selftest

Per state, from the sample (at most --sample PDFs: the latest versions of active bills whose stored text
is 300 characters or fewer (up to a third), the latest versions with more text (a third), and the other
versions stratified over introduced / substitute-or-committee / amended / enrolled-or-act):

* the reader (the function CONVERSION_FUNCTIONS has for the state, wrappers unwrapped);
* documents sampled; documents whose STORED text (the database's length, spacing aside) or whose text read by the
  reader as it is now is under 10% of the full `pdftotext` reading;
* share of lines lost overall: the reader's text against the full reading, spacing and margin numbers
  aside, lines that repeat 3 times or more (running headers) and one-to-three-character lines left out;
  and the share of BODY lines lost: lines between a paper's first and last margin-numbered line, or every
  line when under 3% are numbered (a cover page or signature block the numbered reader leaves out is not body);
* LATEST versions of active bills (a session with `active` true) at 300 characters or fewer, exact count,
  and of the sampled ones how many are truncated (half or more of the body lines lost by the reader as it is
  now), how many only need a refresh (the stored text is far shorter than the reader's text now) and how many
  are real short papers; the same tests on the latest versions with more text, scaled up, as the estimated
  latest versions truncated / refresh-only / partial (5% or more of the body lines lost);
* the worst paper shapes: version kind x bill type, ranked by lost lines.

A struck-text state (al, ga, id, la, nc, nd, ne, nv, me, wa) is compared with the full reading of the PDF
with its struck words removed (`al.without_struck_text`), so removed words are not counted as lost. Oregon's
bracketed deletions are not removed from the comparison, so Oregon's loss is over-stated by them.

Known limits: a line counts as kept when its text (spacing aside) occurs anywhere in the reader's text, and a line
that occurs three or more times in a paper is skipped as a running header, so the lost-line figures are a lower
bound (a repeated body line or a short line inside retained text hides). "Latest" is one version per bill, by
core's `version_sort_key`. The latest-version counts at 300 characters or fewer are exact; the truncated /
refresh-only / real-short split is of the sampled ones (the sample size is in the table), and the "est." columns
scale the sample to all latest versions (a few documents give a rough number, not a count).
"""

import argparse
import collections
import csv
import json
import os
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SQL = """select coalesce(nullif(split_part(split_part(s.jurisdiction_id, 'state:', 2), '/', 1), ''), 'us') st, j.name jname, s.identifier sess,
 d.bill_id, b.identifier ident, d.id, d.version_note, d.version_date, d.source_url, length(d.raw_text) n, length(regexp_replace(d.raw_text, '[[:space:]]', '', 'g')) ns,
 coalesce(d.is_bill_version, true) isv, d.is_error, d.media_type, coalesce(d.archive_location, '') loc
from ddp_bill_version_document d
join opencivicdata_bill b on b.id = d.bill_id
join opencivicdata_legislativesession s on s.id = b.legislative_session_id and s.active
join opencivicdata_jurisdiction j on j.id = s.jurisdiction_id
where coalesce(nullif(split_part(split_part(s.jurisdiction_id, 'state:', 2), '/', 1), ''), 'us') in ({states})
order by d.id"""
COLS = "st jname sess bill ident id note date url n ns isv err mt loc".split()

STRUCK = {"al", "ga", "id", "la", "nc", "nd", "ne", "nv", "me", "wa"}
NUM = re.compile(r"^\s*\d+\s+(.*)")
SHORT = 300
PLAIN_BELOW = 0.03  # a paper with a smaller share of numbered lines is treated as unnumbered
KINDS = ("introduced", "substitute", "amended", "enrolled")


def kind(note: str) -> str:
    n = note.lower()
    if re.search(r"enrolled|chapter|\bact\b|public law|signed|chaptered|session law|approved", n):
        return "enrolled"
    if re.search(r"substitute|committee|\bcs\b|\bscs\b|\bhcs\b|reported|\bsub\b", n):
        return "substitute"
    if re.search(r"amend|engross|reprint|perfect|revised|floor|third|rereferred|reengross", n):
        return "amended"
    return "introduced"


def bill_type(ident: str) -> str:
    return re.sub(r"[^A-Z]", "", ident.upper().split(" ")[0]) or "?"


def squeeze(s: str) -> str:
    return "".join(s.split())


def running_key(t: str) -> str:
    """A line's key for finding running headers and footers: itself, with its digits blanked when it
    says "page" (so "Page 1 of 3" and "Page 2 of 3" are one footer)."""
    return re.sub(r"\d", "0", t) if "page" in t.lower() else t


def measure(full: str, kept: str) -> dict:
    """How much of the full reading `full` the text `kept` holds. Margin numbers, spacing and short or
    repeated lines (running headers and footers, see `running_key`) are not counted. A lost line is BODY when
    it lies between the first and the last margin-numbered line of the paper (a cover page before the
    first numbered line and a signature block after the last are not), or when fewer than 3% of the
    paper's lines are numbered (then every line is body)."""
    lines = []
    for raw in full.split("\n"):
        m = NUM.match(raw)
        lines.append((squeeze(m.group(1) if m else raw), m is not None))
    count = collections.Counter(running_key(t) for t, _ in lines)
    numbered_at = [i for i, (_, numbered) in enumerate(lines) if numbered]
    filled = sum(1 for t, _ in lines if t)
    if len(numbered_at) >= PLAIN_BELOW * filled and numbered_at:
        first, last = numbered_at[0], numbered_at[-1]
    else:  # no numbered lines, or a stray few in a paper that has none (Missouri substitutes): all body
        first, last = 0, len(lines)
    kept_sq = squeeze(kept)
    kept_lines = {squeeze(x) for x in kept.split("\n")}
    total = lost = unnum_total = unnum_lost = chars = chars_lost = body = body_lost = 0
    examples = []
    for i, (t, numbered) in enumerate(lines):
        if len(t) <= 3 or count[running_key(t)] >= 3:
            continue
        in_body = first <= i <= last
        total += 1
        body += in_body
        chars += len(t)
        unnum_total += not numbered
        if t not in kept_lines and t not in kept_sq:
            lost += 1
            body_lost += in_body
            chars_lost += len(t)
            unnum_lost += not numbered
            if in_body and len(examples) < 6:
                examples.append(t[:80])
    return dict(lines=total, lost=lost, body=body, body_lost=body_lost, numbered=len(numbered_at),
                unnum_lines=unnum_total, unnum_lost=unnum_lost, chars=chars, chars_lost=chars_lost,
                kept_chars=len(kept_sq), full_chars=sum(len(t) for t, _ in lines), examples=examples)


def local_path(root: str, st: str, loc: str) -> str:
    if "bills/raw/" in loc:
        return os.path.join(root, loc.split("bills/raw/", 1)[1])
    return os.path.join(root, st, loc)


def read_one(job: tuple) -> dict:
    st, path, row = job
    from openstates.fulltext import CONVERSION_FUNCTIONS, al
    from openstates.fulltext.utils import pdfdata_to_text

    out = dict(row)
    try:
        data = Path(path).read_bytes()
    except OSError:
        out["missing"] = True
        return out
    meta = {"url": row["url"], "media_type": "application/pdf", "title": "",
            "jurisdiction_id": f"ocd-jurisdiction/country:us/state:{st}/government"}
    try:
        full = pdfdata_to_text(al.without_struck_text(data) if st in STRUCK else data)
        kept = CONVERSION_FUNCTIONS[st]["application/pdf"](data, meta)
    except Exception as e:  # reported, not raised
        out["error"] = f"{type(e).__name__}: {e}"[:150]
        return out
    out.update(measure(full, kept))
    out["now_chars"] = len(squeeze(kept))
    out["head"] = " ".join(full.split())[:70]
    return out


def reader_name(st: str) -> str:
    from openstates.fulltext import CONVERSION_FUNCTIONS

    f = CONVERSION_FUNCTIONS.get(st, {}).get("application/pdf")
    names, wraps = [], []
    while f is not None:
        names.append(getattr(f, "__name__", repr(f)))
        f = getattr(f, "__wrapped__", None)
    if len(names) > 1:
        wraps = names[:-1]
    base = names[-1] if names else "none"
    base = {"extract_line_numbered_pdf": "line-numbered", "extract_sometimes_numbered_pdf": "sometimes-numbered"}.get(base, base)
    return base + (" + " + "/".join(wraps) if wraps else "")


def pick(rows: list, rng: random.Random, n: int) -> list:
    return rng.sample(rows, n) if len(rows) > n else list(rows)


def plan(rows: list, st: str, quota: int, seed: int) -> dict:
    from openstates.utils.version_ordering import origin_chamber, version_sort_key

    rng = random.Random(f"{seed}{st}")
    versions = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        if r["isv"] == "t":
            versions[r["bill"]][r["note"]].append(r)
    latest, other = [], []
    for bill, notes in versions.items():
        keyed = []
        for note, docs in notes.items():
            d0 = docs[0]
            keyed.append((version_sort_key(note, d0["date"] or None, [d["url"] for d in docs],
                                           origin_chamber(d0["ident"]), d0["jname"]), note))
        keyed.sort()
        for i, (_, note) in enumerate(keyed):
            docs = notes[note]
            pdfs = [d for d in docs if d["mt"] == "application/pdf"]
            good_sibling = any(d["mt"] != "application/pdf" and int(d["n"]) > SHORT and d["err"] != "t" for d in docs)
            for d in pdfs:
                d["html_sibling"] = good_sibling
                (latest if i == len(keyed) - 1 else other).append(d)
    short = [d for d in latest if int(d["n"]) <= SHORT and not d["html_sibling"]]
    long_ = [d for d in latest if int(d["n"]) > SHORT or d["html_sibling"]]
    third = quota // 3
    sample_a, sample_b = pick(short, rng, third), pick(long_, rng, third)
    rest = quota - len(sample_a) - len(sample_b)
    by_kind = {k: [d for d in other if kind(d["note"]) == k] for k in KINDS}
    sample_c, left = [], rest
    for i, k in enumerate(KINDS):
        take = min(len(by_kind[k]), left // (len(KINDS) - i))
        got = pick(by_kind[k], rng, take)
        sample_c += got
        left -= len(got)
    return dict(a=sample_a, b=sample_b, c=sample_c, n_a=len(short), n_b=len(long_), n_other=len(other),
                n_bills=len(versions))


def truncated(r: dict) -> bool:
    """Half or more of the paper's body lines are not in the text the reader returns."""
    return r["body_lost"] >= 3 and r["body_lost"] * 2 >= r["body"]


def partial(r: dict) -> bool:
    return not truncated(r) and r["body_lost"] >= 1 and r["body_lost"] * 20 >= r["body"]


def stale(r: dict) -> bool:
    """The stored text is far shorter than what the reader returns now: a refresh-extraction fixes it."""
    return not truncated(r) and int(r["ns"]) * 2 <= r["now_chars"] - 50


def summarize(st: str, pl: dict, res: list) -> dict:
    ok = [r for r in res if "lines" in r]
    s = dict(st=st, reader=reader_name(st), sampled=len(res), measured=len(ok),
             missing=sum(1 for r in res if r.get("missing")), errors=sum(1 for r in res if "error" in r),
             latest_bills=pl["n_bills"])
    s["under10_stored"] = sum(1 for r in ok if r["full_chars"] and int(r["ns"]) < 0.10 * r["full_chars"])
    s["under10_now"] = sum(1 for r in ok if r["full_chars"] and r["now_chars"] < 0.10 * r["full_chars"])
    tl, ll = sum(r["lines"] for r in ok), sum(r["lost"] for r in ok)
    s["lines_lost_pct"] = round(100 * ll / tl, 1) if tl else 0.0
    tb, lb = sum(r["body"] for r in ok), sum(r["body_lost"] for r in ok)
    s["body_lost_pct"] = round(100 * lb / tb, 1) if tb else 0.0
    a = [r for r in pl["a"] if "lines" in r]
    b = [r for r in pl["b"] if "lines" in r]
    s["latest_short_exact"], s["latest_short_sampled"] = pl["n_a"], len(a)
    s["latest_short_truncated"] = sum(1 for r in a if truncated(r))
    s["latest_short_stale"] = sum(1 for r in a if stale(r))
    s["latest_short_real"] = len(a) - s["latest_short_truncated"] - s["latest_short_stale"]

    def scale(n: int, sample: list, test) -> int:
        return round(n * sum(1 for r in sample if test(r)) / len(sample)) if sample else 0

    s["latest_long_exact"] = pl["n_b"]
    s["est_truncated_latest"] = scale(pl["n_a"], a, truncated) + scale(pl["n_b"], b, truncated)
    s["est_stale_latest"] = scale(pl["n_a"], a, stale) + scale(pl["n_b"], b, stale)
    s["est_partial_latest"] = scale(pl["n_a"], a, partial) + scale(pl["n_b"], b, partial)
    shapes = collections.defaultdict(lambda: [0, 0])
    for r in ok:
        sh = shapes[(kind(r["note"]), bill_type(r["ident"]))]
        sh[0] += 1
        sh[1] += r["body_lost"]
    s["worst_shapes"] = [f"{k[0]} {k[1]}: {v[1]} body lines lost in {v[0]} docs" for k, v in
                         sorted(shapes.items(), key=lambda kv: -kv[1][1])[:3] if v[1]]
    return s


def report(summaries: list) -> None:
    summaries.sort(key=lambda s: -(s["est_truncated_latest"] + s["est_stale_latest"] + s["est_partial_latest"]))
    print("| state | reader | bills | sampled | <10% stored / now | lines lost % (body lines lost %) | latest <=300: exact, sampled, truncated, refresh-only, real short | est. latest versions: truncated, refresh-only, partial | worst shapes |")
    print("|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        print(f"| {s['st']} | {s['reader']} | {s['latest_bills']} | {s['measured']}/{s['sampled']} "
              f"| {s['under10_stored']} / {s['under10_now']} | {s['lines_lost_pct']} ({s['body_lost_pct']}) "
              f"| {s['latest_short_exact']}, {s['latest_short_sampled']}, {s['latest_short_truncated']}, "
              f"{s['latest_short_stale']}, {s['latest_short_real']} "
              f"| {s['est_truncated_latest']}, {s['est_stale_latest']}, {s['est_partial_latest']} "
              f"| {'; '.join(s['worst_shapes'])} |")


def selftest() -> int:
    numbered = "\n".join(f"{i} line number {i} of a bill text" for i in range(1, 21))
    full = "Header\nSection A.\n" + numbered + "\nplain paragraph that has no margin number\n"
    kept_numbered_only = "\n".join(f"line number {i} of a bill text" for i in range(1, 21))
    m = measure(full, kept_numbered_only)
    # the cover lines before the first numbered line and the paragraph after the last are lost, not body
    assert (m["lines"], m["lost"], m["unnum_lost"], m["body"], m["body_lost"]) == (23, 3, 3, 20, 0), m
    assert not truncated(m) and not partial(m)
    # Missouri's House quirk: an unnumbered "Section A." line between numbered lines is body
    inner = "1 first numbered line of text\nSection A. an unnumbered opening line\n2 second numbered line of text"
    m = measure(inner, "first numbered line of text\nsecond numbered line of text")
    assert (m["body"], m["body_lost"]) == (3, 1), m
    # a paper with no margin numbers read by the numbered reader (Kentucky acts): everything is body and lost
    plain = "\n".join(f"unnumbered sentence number {i} of a chaptered act" for i in range(10))
    m = measure(plain, "")
    assert (m["numbered"], m["body"], m["body_lost"]) == (0, 10, 10) and truncated(m), m
    assert m["kept_chars"] < 0.1 * m["full_chars"]
    # a stray margin-looking number in a long unnumbered paper (a section number at a line start) does not
    # make it a numbered one: everything is still body (Missouri's Senate substitutes)
    long_plain = "\n".join(f"unnumbered sentence number {i} of a committee substitute" for i in range(100))
    m = measure(long_plain + "\n536 are nonseverable and if any of the powers vested", "are nonseverable and if any of the powers vested")
    assert (m["numbered"], m["body"], m["body_lost"]) == (1, 101, 100) and truncated(m), m
    # the same paper read whole loses nothing; a running header (page digits aside) is not counted
    assert measure(plain, plain)["lost"] == 0
    rep = "\n".join(["Page 1 header text here", "Page 2 header text here", "Page 3 header text here", "body line one"])
    assert measure(rep, "body line one")["lost"] == 0
    # 5% of the body lines lost is partial, not truncated
    many = "\n".join(f"{i} numbered body line number {i} with words" for i in range(1, 41))
    m = measure(many, "\n".join(f"numbered body line number {i} with words" for i in range(3, 41)))
    assert m["body_lost"] == 2 and partial(m) and not truncated(m), m
    assert partial(measure(inner, "first numbered line of text\nsecond numbered line of text"))
    assert kind("Acts Chapter 12") == "enrolled" and kind("4239S.01I - Introduced") == "introduced"
    assert kind("Senate Committee Substitute") == "substitute" and kind("Engrossed") == "amended"
    assert bill_type("SCR 21") == "SCR" and bill_type("HB 1") == "HB"
    print("selftest ok")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("states", nargs="*")
    p.add_argument("--sql", action="store_true", help="print the SELECT for the given states and exit")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--rows")
    p.add_argument("--root", default=os.environ.get("ARCHIVE_ROOT_DIR", "/Volumes/DDP-HOT/bills/raw"))
    p.add_argument("--sample", type=int, default=300)
    p.add_argument("--seed", type=int, default=911)
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    p.add_argument("--details")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.sql:
        print(SQL.format(states=", ".join(f"'{s}'" for s in a.states)))
        return 0
    by_state = collections.defaultdict(list)
    with open(a.rows, newline="") as f:
        for rec in csv.reader(f):
            r = dict(zip(COLS, rec))
            if r["st"] in a.states:
                by_state[r["st"]].append(r)
    summaries, details = [], []
    with ProcessPoolExecutor(a.jobs) as pool:
        for st in a.states:
            pl = plan([r for r in by_state[st]], st, a.sample, a.seed)
            picked = [(k, d) for k in "abc" for d in pl[k]]
            jobs = [(st, local_path(a.root, st, d["loc"]) if d["loc"] else "", d) for _, d in picked]
            res = list(pool.map(read_one, jobs, chunksize=4))
            for k in "abc":
                pl[k] = [r for (kk, _), r in zip(picked, res) if kk == k]
            for (k, _), r in zip(picked, res):
                details.append({"group": k, **{x: r[x] for x in r if x != "head"}, "head": r.get("head", "")})
            summaries.append(summarize(st, pl, res))
    report(summaries)
    if a.details:
        with open(a.details, "w") as f:
            for d in details:
                f.write(json.dumps(d) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
