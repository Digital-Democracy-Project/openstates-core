#!/usr/bin/env python
import os
import re
import hashlib
import difflib
import typing
import sys
import csv
import json
import math
import subprocess
import textwrap
import warnings
import click
import requests
import scrapelib
import time
from pathlib import Path
from urllib.parse import urlparse
from django.contrib.postgres.search import SearchVector  # type: ignore
from django.db import transaction, IntegrityError  # type: ignore
from django.db.models import Count  # type: ignore
from django.utils import timezone  # type: ignore
from openstates.utils.django import init_django
from openstates.utils import jid_to_abbr, abbr_to_jid
from openstates.fulltext import (
    get_extract_func,
    DoNotDownload,
    CONVERSION_FUNCTIONS,
    Metadata,
)
from ..utils.instrument import Instrumentation
from ..utils.cookie_provider import (
    BLOCK_PAGE_MARKERS as _BLOCK_PAGE_MARKERS,
    WafBlockDetected,
    content_matches_block_markers,
)
from ..utils.mi_cookies import MI_COOKIE_PROVIDER

stats = Instrumentation()
# disable SSL validation and ignore warnings
scraper = scrapelib.Scraper(verify=False)
scraper.user_agent = "Mozilla"
# Match FL's own scraper's resilience settings (scrapers/fl/bills.py) instead of scrapelib's
# bare defaults (0 retries) -- this scraper hits the same flaky state legislature sites FL's
# scraper does, so it should retry the same way rather than giving up on the first failure.
scraper.retry_attempts = 5
scraper.retry_wait_seconds = 5
warnings.filterwarnings("ignore", module="urllib3")


def get_raw_dir() -> Path:
    return Path(__file__).parent / ".." / "fulltext" / "raw"


MIMETYPES = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/msword": "doc",
    "application/rtf": "rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}

# Phase 2 (PLAN-bill-document-provenance.md): S3 Glacier Deep Archive upload + verify.
# Always go through the sudo-gated proxy wrapper -- no raw AWS credentials or CLI exist on
# this box by design (see ddp-infra/Production_S3_Wrappers.md).
S3_BILL_ARCHIVE_WRAPPER = "/Users/agentsmith/bin/ddp-prod-s3-bill-archive"
S3_BILL_ARCHIVE_BUCKET = "ddp-bill-archive"

# Found 2026-07-28: legislature.mi.gov started serving a CAPTCHA/rate-limit challenge page
# (HTTP 200, "Validation request" title) in place of every requested document mid-run. Nothing
# about that response looks wrong at the transport layer, so it was archived and S3-uploaded as
# if it were the real bill text -- 236 documents, silently. These two checks catch that class of
# bug: a binary media_type whose actual bytes don't match that format's own magic number at all,
# or any response containing a known vendor challenge-page fingerprint. Not exhaustive by
# design -- this catches "the site is lying about what it sent us", not every possible malformed
# document.
_BINARY_MAGIC_BYTES = {
    "application/pdf": (b"%PDF-",),
    "application/msword": (b"\xd0\xcf\x11\xe0",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        b"PK\x03\x04",
    ),
}
# _BLOCK_PAGE_MARKERS itself now lives in openstates.utils.cookie_provider (OPEN-19) so
# scrapers and this archiver share one block-detection heuristic instead of each keeping
# its own copy that can drift out of sync.


def _block_page_reason(data: bytes, media_type: str) -> typing.Optional[str]:
    magics = _BINARY_MAGIC_BYTES.get(media_type)
    if magics and not any(data.startswith(m) for m in magics):
        return f"expected {media_type} magic bytes, got {data[:16]!r} instead"
    sniff = data[:2048].lower()
    for marker in _BLOCK_PAGE_MARKERS:
        if marker in sniff:
            return f"content matches known block-page marker {marker!r}"
    return None


def _fetch_bytes(url: str) -> bytes:
    """
    GET url via the module-level `scraper` and return its content.

    Only legislature.mi.gov (OPEN-19) is wired to the cached WAF cookies -- every other
    jurisdiction's fetch is completely unchanged. Deliberately scoped to this function
    (used by archive_bill_versions(), the path run-archive.sh actually calls) and not the
    older download()/update_bill() paths used by the separate `sample`/`update` commands --
    out of scope for this ticket, not an oversight.
    """
    if "legislature.mi.gov" in urlparse(url).netloc:

        def do_request(cookies: dict, user_agent: str) -> requests.Response:
            try:
                # OPEN-23: attach the real User-Agent MI_COOKIE_PROVIDER captured
                # alongside these same cookies -- this archiver previously sent no
                # MI-specific User-Agent at all, the same cookie/identity mismatch bug
                # fixed in scrapers/mi/bills.py and events.py.
                resp = scraper.request(
                    "GET",
                    url,
                    allow_redirects=True,
                    cookies=cookies,
                    headers={"User-Agent": user_agent},
                )
            except requests.exceptions.ConnectionError as e:
                raise WafBlockDetected(str(e)) from e
            if content_matches_block_markers(resp.content):
                raise WafBlockDetected(
                    "response matched known WAF block-page heuristic"
                )
            return resp

        return MI_COOKIE_PROVIDER.fetch_with_retry(do_request).content

    return scraper.request("GET", url, allow_redirects=True).content


def _cleanup(text: str) -> str:
    # strip nulls
    return text.replace("\0", "")


def download(
    version: dict[str, str]
) -> tuple[typing.Optional[str], typing.Optional[bytes]]:
    abbr = jid_to_abbr(version["jurisdiction_id"])
    ext = MIMETYPES[version["media_type"]]
    filename = str(
        get_raw_dir()
        / f'{abbr}/{version["session"]}-{version["identifier"]}-{version["note"]}.{ext}'
    )
    filename.replace("#", "__")

    # FL "dh key too small" error due to bad Diffie Hellman key on the server side
    ciphers_list_addition = None
    if abbr == "fl":
        ciphers_list_addition = "HIGH:!DH:!aNULL"

    if not os.path.exists(filename):
        try:
            os.makedirs(os.path.dirname(filename))
        except OSError:
            pass
        try:
            _, resp = scraper.urlretrieve(
                version["url"], filename, ciphers_list_addition=ciphers_list_addition
            )
        except Exception:
            click.secho(f"could not fetch {version['url']}", fg="yellow")
            return None, None

        return filename, resp.content
    else:
        with open(filename, "rb") as f:
            return filename, f.read()


def extract_to_file(
    filename: str, data: bytes, version: Metadata
) -> tuple[typing.Union[None, str, typing.Type[DoNotDownload]], int]:
    text: typing.Optional[str]
    try:
        func = get_extract_func(version)
        if func == DoNotDownload:
            return DoNotDownload, 0
        else:
            text = func(data, version)
    except Exception as e:
        stats.write_stats(
            [
                {
                    "metric": "failed_text_extractions",
                    "fields": {"total": 1},
                    "tags": {"jurisdiction": version["jurisdiction_id"]},
                }
            ]
        )
        click.secho(f"exception processing {version['url']}: {e}", fg="red")
        text = None

    if not text:
        return None, 0

    text_filename = filename.replace("raw/", "text/") + ".txt"
    try:
        os.makedirs(os.path.dirname(text_filename))
    except OSError:
        pass
    with open(text_filename, "w") as f:
        f.write(text)

    return text_filename, len(text)


def update_bill(bill: typing.Any) -> int:
    from openstates.data.models import SearchableBill

    try:
        latest_version = bill.versions.order_by("-date", "-note").prefetch_related(
            "links"
        )[0]
        links = latest_version.links.all()
    except IndexError:
        links = []

    # check if there's an old entry and we can use it
    # if bill.searchable:
    #     if bill.searchable.version_id == latest_version.id and not bill.searchable.is_error:
    #         return      # nothing to do
    #     bill.searchable.delete()

    # FL "dh key too small" error due to bad Diffie Hellman key on the server side
    jurisdiction = bill.legislative_session.jurisdiction.name
    ciphers_list_addition = None
    if jurisdiction == "Florida":
        ciphers_list_addition = "HIGH:!DH:!aNULL"

    # iterate through versions until we extract some good text
    is_error = True
    raw_text = ""
    link = None
    for link in links:
        # TODO: if we need other exceptions, change this to a pluggable interface
        if (
            bill.legislative_session.jurisdiction_id
            == "ocd-jurisdiction/country:us/state:ca/government"
        ):
            # move CA query string onto a docs-proxy query string for working PDF extraction
            old_url = link.url
            new_url = "http://docs-proxy.openstates.org/ca?" + link.url.split("?")[1]
            print(f"{old_url} => {new_url}")
            link.url = new_url
        metadata: Metadata = {
            "url": link.url,
            "media_type": link.media_type,
            "title": bill.title,
            "jurisdiction_id": bill.legislative_session.jurisdiction_id,
        }
        func = get_extract_func(metadata)
        if func == DoNotDownload:
            continue
        try:
            data = scraper.request(
                "GET",
                link.url,
                allow_redirects=True,
                ciphers_list_addition=ciphers_list_addition,
            ).content
        except Exception:
            continue
        try:
            raw_text = func(data, metadata)
        except Exception as e:
            click.secho(f"exception processing {metadata['url']}: {e}", fg="red")

        # TODO: clean up whitespace
        raw_text = _cleanup(raw_text)

        if raw_text:
            is_error = False
            break

    sb = SearchableBill.objects.create(
        bill=bill,
        version_link=link,
        all_titles=bill.title,  # TODO: add other titles
        raw_text=raw_text,
        is_error=is_error,
        search_vector="",
    )
    return sb.id


def _archive_path(
    bill: typing.Any, version_note: str, version_date: str, url: str, ext: str
) -> str:
    """
    Build the permanent archive path for one bill version's document.

    Keyed by version_date + a hash of the source url (not just version_note) so it matches
    BillVersionDocument's own uniqueness key exactly — a single version_note alone isn't unique
    within a bill (PLAN-bill-document-provenance.md, Phase 1: a version can have more than one
    file, e.g. a PDF and an HTML copy of "Introduced", and a path keyed on version_note alone
    would let one silently overwrite the other on disk).

    Three path segments are for human browsability, not identity, added 2026-07-24: the top-level
    "bills" segment (DDP-HOT is expected to hold other document types over time, not just bill
    text), a "{chamber}" segment (found via real DDP-HOT data: without it, USA's House and Senate
    bills -- scraped as two entirely separate runs -- were being jumbled into one flat "119"
    folder, with chamber visible only implicitly via the HR/S-style identifier prefix; applied to
    every jurisdiction for consistency, not just USA, even though state identifiers already hint
    at chamber), and the bill folder's "{identifier}--{uuid}" prefix (so `ls` reveals which bill
    you're looking at). The actual identity/uniqueness still rests entirely on the bill's stable
    UUID and the (version_note, version_date, url) key below — chamber and identifier are cosmetic
    and can go stale (a rare chamber switch or mid-session renumbering) without affecting the
    skip-check or the DB.
    """
    from openstates import settings

    abbr = jid_to_abbr(bill.legislative_session.jurisdiction_id)
    session = bill.legislative_session.identifier
    # bill.id is "ocd-bill/<uuid>" — strip the prefix so it's usable as a bare path segment.
    bill_id = bill.id.split("/")[-1]
    chamber = (bill.from_organization.classification or "").strip() or "unknown"
    safe_identifier = re.sub(r"[^A-Za-z0-9_-]+", "", bill.identifier) or "bill"
    bill_dir = f"{safe_identifier}--{bill_id}"
    safe_note = re.sub(r"[^A-Za-z0-9_-]+", "_", version_note).strip("_") or "version"
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    # version_date is often blank in real scraped data (confirmed against live VA bills) —
    # omit it rather than leaving a stray leading separator in the filename.
    date_part = f"{version_date}-" if version_date else ""
    filename = f"{date_part}{safe_note}-{url_hash}.{ext}"
    return os.path.join(
        settings.ARCHIVE_ROOT_DIR,
        "bills",
        "raw",
        abbr,
        session,
        chamber,
        bill_dir,
        filename,
    )


def _s3_object_key(archive_path: str) -> str:
    """
    Object key mirrors the local ARCHIVE_ROOT_DIR-relative path 1:1, so the S3 layout matches
    the local archive's and both are equally browsable.
    """
    from openstates import settings

    return os.path.relpath(archive_path, settings.ARCHIVE_ROOT_DIR)


def _upload_and_verify(
    path: str, object_key: str, local_md5: str
) -> typing.Optional[str]:
    """
    Upload one archived document to S3 Glacier Deep Archive and verify the upload via ETag
    (PLAN-bill-document-provenance.md, Phase 2 -- verification mechanism revised 2026-07-25).

    The bill-archive proxy (`ddp-prod-s3-bill-archive`, sudo-gated -- see
    ddp-infra/Production_S3_Wrappers.md) uploads directly to DEEP_ARCHIVE with no normal-class
    staging step, and has no download command at all: a real Deep Archive object needs a ~12hr
    restore request before it's readable, so "upload, read back, recompute hash" (the original
    Phase 2 design) is structurally unavailable here, not just slow. Instead: for a single-part
    upload, S3 guarantees ETag is the plain hex MD5 of exactly the bytes it received and stored
    -- a real, independent, server-computed check, just a weaker one than a full read-after-write.
    A multipart-style ETag (a "-N" suffix -- a hash of hashes, not a plain MD5) can't be compared
    this way at all and is treated the same as a verification failure, not silently accepted.

    Open assumption, not yet independently confirmed (see plan's Risk Register): that the
    proxy's `put-stream` always performs a single-part PutObject regardless of file size. Bill
    documents (PDFs/HTML) are expected to stay well under any multipart threshold.

    Returns the s3:// URI on a verified match; None on any upload failure, ETag mismatch, or a
    multipart ETag -- the caller leaves `archive_location`/`archived_at` unset in every None
    case, so an unverified upload is never recorded as archived.
    """
    try:
        subprocess.run(
            [S3_BILL_ARCHIVE_WRAPPER, "put", path, object_key],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"S3 upload failed for {object_key}: {e.stderr.strip()}", fg="red")
        return None
    except OSError as e:
        click.secho(f"S3 upload failed for {object_key}: {e}", fg="red")
        return None

    try:
        info_proc = subprocess.run(
            [S3_BILL_ARCHIVE_WRAPPER, "info", object_key],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(info_proc.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as e:
        click.secho(f"S3 verify failed for {object_key}: {e}", fg="red")
        return None

    etag = info.get("ETag", "").strip('"')
    if not etag:
        click.secho(
            f"S3 verify failed for {object_key}: no ETag in info response", fg="red"
        )
        return None
    if "-" in etag:
        click.secho(
            f"S3 upload for {object_key} used multipart (ETag={etag}); cannot verify via "
            "ETag-as-MD5, treating as unverified",
            fg="yellow",
        )
        return None
    if etag != local_md5:
        click.secho(
            f"S3 ETag mismatch for {object_key}: local md5={local_md5} etag={etag}",
            fg="red",
        )
        return None

    return f"s3://{S3_BILL_ARCHIVE_BUCKET}/{object_key}"


# OPEN-34: archive_bill_versions() used to walk bill.versions.all() with no explicit
# ordering and trust that walk order for diff_from_previous_version's "prior_text" lineage.
# BillVersion has no Meta.ordering and no timestamp column, and BillVersion.date is blank
# 100% of the time for every state jurisdiction audited (FL/MI/AZ/UT/WA/VA -- confirmed
# against real archived data, not just "frequently" blank as originally suspected); only US
# federal populates it (~99.4%). So the walk order was whatever Postgres happened to return
# for an unordered SELECT, which in practice tracks DB insertion order -- confirmed real and
# inconsistent across jurisdictions via a ~10-12-bill-per-jurisdiction sample:
#   - FL: forward in 9/12 sampled bills, but "Filed" (the introduced stage) was NOT first in
#     3/12 (e.g. real bills "SB 1668", "SB 1220") -- forward is the majority pattern, not a
#     guarantee.
#   - MI: forward ONLY for Resolutions and for the rare Bill with no Substitute version. Any
#     Bill with a Substitute gets that Substitute inserted BEFORE "House/Senate Introduced
#     Bill" in walk order (8/12 sampled bills) -- the ticket's original single clean example
#     (HB 4420) had no substitutes and wasn't representative of the common case.
#   - AZ: forward in 11/12 sampled bills; one real exception where a floor-amendment-style
#     version landed first.
#   - VA: backward -- already confirmed at real scale by OPEN-33 (604 affected rows, full
#     audit), not re-derived here.
#   - UT: does NOT fit a simple reversal at all. "Enrolled" appears at wildly inconsistent
#     positions across real bills (immediately after Introduced, mid-sequence, or followed by
#     more Substitutes) -- closer to WA's non-binary case than to a clean "backward" state.
#   - US (federal): backward in 10/12 sampled bills, but BillVersion.date is reliably
#     populated (~99.4%) -- a real date-based fix, not a workaround, fully covers US.
#   - WA: root-caused, not left ambiguous. scrapers/wa/bills.py's _load_versions() fetches
#     one page per bill_type ("Bills", "Resolutions", ..., "Passed Legislature" -- in that
#     dict order), so "X Passed Legislature" documents are structurally always walked last
#     (a deterministic code-order effect, not scrambled DB rows or interleaved re-scrapes).
#     Within the "Bills" page, WA's own site lists "Engrossed <N> Substitute" before the
#     plain "<N> Substitute" it amends, and the bare introduced "Bill" near the end instead
#     of first -- deterministic, just not chronological.
#
# A static per-jurisdiction "reverse" flag (Option A in the ticket) is therefore not
# supportable by this data -- no jurisdiction sampled is 100% one direction, and MI/UT/WA
# aren't even binary. The fix below never trusts DB walk order at all: it ranks each version
# by (1) BillVersion.date when it's actually populated and parses as a date -- covers US
# federal outright and any future jurisdiction that starts populating it -- and otherwise (2)
# a content-based stage rank built directly from the real version_note vocabulary above.
# A version whose note matches neither is never guessed into a position: it's excluded from
# the diff lineage entirely (see _UNKNOWN_STAGE below) rather than risking a backward diff,
# per the ticket's own framing that a wrong-direction diff is worse than a missing one.
_STAGE_INTRODUCED = 0
_STAGE_AMENDMENT = 1
_STAGE_CHAMBER_PASSAGE = 2
_STAGE_FINAL_PASSAGE = 3
_STAGE_ENACTED = 4
_STAGE_UNKNOWN = 99  # excluded from diff lineage entirely -- see _version_sort_key()

_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

_DATE_RE = re.compile(r"\A\d{4}(-\d{2}(-\d{2})?)?\Z")


def _extract_ordinal(note: str) -> float:
    """
    Best-effort numeric ordinal embedded in a version_note, used to rank same-stage numbered
    variants against each other (MI's "Substitute (S-2)", UT's "Substitute #3", WA's "Second
    Substitute", FL's "c2"/"e2"). 0.0 if no ordinal is found -- the unnumbered/first-of-its-
    kind case (FL's "c1", WA's plain "Substitute Bill" with no ordinal word).

    MI's "(S-1)"/(H-2)" parenthesized number is checked first and takes priority over a
    trailing "- N" suffix on the same note (a second file for that *same* substitute stage,
    e.g. "Substitute (S-1) - 2" -- a minor tiebreak, not a different amendment stage; folded
    in as a small fraction so it sorts immediately after "Substitute (S-1)" rather than being
    conflated with "Substitute (S-2)").
    """
    lowered = note.lower()

    paren = re.search(r"\([sh]-(\d+)\)", lowered)
    if paren:
        base = float(paren.group(1))
        tail = re.search(r"\)\s*-\s*(\d+)\s*\Z", lowered)
        return base + (int(tail.group(1)) / 100.0 if tail else 0.0)

    for word, value in _ORDINAL_WORDS.items():
        if word in lowered:
            return float(value)

    m = (
        re.search(r"#\s*(\d+)\b", note)
        or re.search(r"\b[a-z](\d+)\b", lowered)
        or re.search(r"(\d+)\s*\Z", note)
    )
    if m:
        return float(m.group(1))
    return 0.0


def _note_stage(note: str) -> tuple:
    """
    Classify a version_note into (stage, ordinal) using the content-based stage table built
    from the OPEN-34 audit (see the comment above archive_bill_versions()). Never looks at
    DB order or position -- purely a function of the note text itself, so it's stable no
    matter what order versions are walked in or what row order Postgres happens to return.
    """
    lowered = note.lower()

    if re.search(
        r"public act|public law|\bchapter|passed legislature|concurred", lowered
    ):
        return (_STAGE_ENACTED, _extract_ordinal(note))

    # Final-passage sub-stages, most-final-first, checked in this specific order since a
    # note can match more than one (e.g. VA's "Governor's Veto Explanation" contains neither
    # "reenroll" nor plain "enroll"). Sub-ranks encode the real chronology confirmed against
    # VA's own examples (OPEN-33/the ticket): Enrolled -> Governor Substitute -> Reenrolled ->
    # Governor's Veto Explanation. Note: "enroll" (no leading \b) deliberately matches
    # "Reenrolled" too ("re" + "enrolled" has no word boundary between them for \benroll to
    # anchor on) -- the explicit "reenroll" check above it takes priority so the two don't
    # collide.
    if "veto" in lowered:
        return (_STAGE_FINAL_PASSAGE, 3.0)
    if "reenroll" in lowered:
        return (_STAGE_FINAL_PASSAGE, 2.0)
    if "governor" in lowered:
        return (_STAGE_FINAL_PASSAGE, 1.0)
    if "enroll" in lowered or re.search(r"\ber\b", lowered):
        return (_STAGE_FINAL_PASSAGE, 0.0)

    if re.match(r"(senate|house)\s*-", lowered):
        # AZ floor/committee-action notes that leak into version_note -- observed after
        # engrossment in every real sample checked (a floor amendment applies to the
        # already-engrossed bill), so rank just after plain chamber-passage. Checked before
        # the generic "engross" test below since these notes sometimes *reference* an
        # engrossed version by name (e.g. "ref Senate Engrossed House Bill") without
        # themselves being one -- a leading "senate -"/"house -" is the more specific,
        # reliable signal for this AZ-specific note shape.
        return (_STAGE_CHAMBER_PASSAGE, 0.5)

    if re.search(r"\be\d+\b", lowered) and not re.search(
        r"substitute|committee", lowered
    ):
        # FL's own engrossed shorthand ("e1", "e2") -- distinct token pattern from the
        # "engross" word check below but the same chamber-passage stage.
        return (_STAGE_CHAMBER_PASSAGE, _extract_ordinal(note))

    if "engross" in lowered and not re.search(r"substitute|committee", lowered):
        # AZ-style whole-bill floor engrossment ("Senate Engrossed Version") -- a later,
        # chamber-passage-level stage, distinct from WA's per-substitute "Engrossed <N>
        # Substitute Bill" handled below.
        return (_STAGE_CHAMBER_PASSAGE, _extract_ordinal(note))

    if re.search(r"conference|\breport|\breferr|placed on calendar|as passed", lowered):
        return (_STAGE_CHAMBER_PASSAGE, _extract_ordinal(note) + 0.25)

    if re.search(r"substitute|amend|comparison|\bc\d+\b", lowered):
        if "engross" in lowered:
            # WA's "Engrossed <N> Substitute Bill" amends that specific substitute number --
            # ranks immediately after it, not after every substitute regardless of number.
            return (_STAGE_AMENDMENT, _extract_ordinal(note) + 0.5)
        return (_STAGE_AMENDMENT, _extract_ordinal(note))

    if re.search(r"introduced|\bfiled\b|\bpb\b|original|^bill$", lowered):
        return (_STAGE_INTRODUCED, _extract_ordinal(note))

    if lowered == "bill text":
        # MA's only version_note until OPEN-37 added a second (scrapers/ma/bills.py's
        # add_version_link("Bill Text", ...)) -- MA has no other stage name for its
        # introduced text, and "bill text" doesn't match "^bill$" (extra word) or any other
        # case above, so without this it fell through to _STAGE_UNKNOWN and was excluded from
        # the diff lineage entirely. Exact match, not a substring check, so this can't
        # accidentally swallow some other jurisdiction's differently-worded note.
        return (_STAGE_INTRODUCED, 0.0)

    return (_STAGE_UNKNOWN, 0.0)


def _version_sort_key(note: str, date: typing.Optional[str]) -> tuple:
    """
    Rank a single version (by its note + date) for chronological ordering, without ever
    trusting the order it was returned from the DB in. See the OPEN-34 comment above
    archive_bill_versions() for the audit this encodes.

    Returns (stage, date-or-empty, ordinal). The macro stage always comes from the note (see
    _note_stage()) -- a real, parseable date is used only as a same-stage tiebreaker, not as
    an override of the note-based stage. This matters for jurisdictions that could have a mix
    of dated and undated versions on the same bill (US federal is ~99.4% dated, not 100%):
    letting a date win globally would make any dated version sort before every undated one
    regardless of true chronology. Confirmed via audit: 0% of state-jurisdiction versions
    (FL/MI/AZ/UT/WA/VA) have a date at all, so this tiebreaker is inert for them and they rely
    entirely on the note-based stage; US federal's real dates resolve same-stage ordering
    (e.g. "Reported to Senate" vs. "Engrossed in Senate") more precisely than the ordinal
    heuristic alone would.

    A note matching none of the known patterns returns stage _STAGE_UNKNOWN -- the caller
    excludes those versions from the diff lineage entirely rather than guessing a position for
    them (see archive_bill_versions()).
    """
    stage, ordinal = _note_stage(note)
    has_date = bool(date) and bool(_DATE_RE.match(date))
    return (stage, date if has_date else "", ordinal)


# OPEN-7: archive_bill_versions()'s diff_from_previous_version, for Washington specifically,
# is dominated by repeated administrative text (tracking code, title, sponsor line, procedural
# "Read first time/Referred to Committee" line) printed at the top of every version and never
# stripped by CONVERSION_FUNCTIONS["wa"]["text/html"] (extractor_for_element_by_xpath("//html"),
# which only runs clean() -- whitespace normalization, no header/footer filtering).
#
# Two things confirmed directly against the real archive (not assumed) reshape this well beyond
# the ticket's own starting point:
#
# 1. CONVERSION_FUNCTIONS["wa"]["application/pdf"] (OPEN-49, landed 2026-08-09) means WA PDF
#    text is no longer always-empty -- and extract_line_numbered_pdf's text_after_line_numbers()
#    already drops every one of these header lines as a side effect (same mechanism that closed
#    Florida's equivalent ticket, OPEN-8), confirmed against every real stage sampled. PDF text
#    needs no boilerplate stripping at all.
# 2. WA's real HTML raw_text has ZERO internal newlines (confirmed via SQL against the live
#    archive: length(text) - length(replace(text, chr(10), '')) = 0 on every sampled row) --
#    extractor_for_element_by_xpath("//html")'s text_content() never reintroduces them, and
#    inter-element joins are sometimes glued with no whitespace at all ("ByRepresentatives...").
#    A single-line string always diffs as "the whole line changed" no matter what's stripped
#    from it, so boilerplate removal alone cannot reduce line-level diff noise -- reconstructing
#    real line boundaries is what actually does. Since prior_text now usually comes from PDF
#    (see the "Prefer text/xml over application/pdf" comment below) while a version's own HTML
#    document still diffs its raw, un-reflowed text against that PDF-sourced prior_text, the two
#    pipelines' wildly different native line-wrapping (print-width columns vs none at all) also
#    needs a common, content-derived line shape before they can align at all -- not just the
#    HTML side.
#
# _clean_wa_text() therefore: normalizes whitespace (so "RCW  64.32.250" from HTML and
# "RCW 64.32.250" from PDF become the same string), strips the known noise substrings
# (non-line-anchored -- matching requires no assumption of surrounding whitespace or an intact
# line, since real data has neither reliably), then rebuilds a stable, content-derived line
# shape (split on sentence-ending punctuation, word-wrapped within each sentence so one
# mid-sentence edit doesn't cascade wrap points through the rest of the document). Deliberately
# omits the ticket's suggested _LEADING_LINE_NUMBER pattern -- verified inert against real WA
# data: PDF text never has it (already stripped upstream by extract_line_numbered_pdf) and HTML
# text never had it to begin with (WA's HTML page doesn't print per-line margin numbers).
#
# Validated against 20 real Washington bills (126 real consecutive version-transition x
# media-type pairs, incl. the 3 largest bills by file size, spanning Introduced/Substitute*/
# Engrossed*/Passed_Legislature stages): mean noise ratio (fraction of the old version's lines
# appearing as a +/- change) drops from 1.000 -> 0.174 for text/html documents and from 0.127 ->
# 0.099 for application/pdf documents; 120/126 pairs improve (106 "meaningfully," >=20% relative
# or >=0.05 absolute), 3 are unchanged (already-identical text), and 3 show a negligible
# regression (<=0.0012 absolute, i.e. <=0.12%) -- each traced to a genuine, tiny, real edit
# (confirmed by reading the actual raw diff) where this reflow's canonical, content-derived line
# boundaries happen to span slightly more physical lines than the original PDF's arbitrary
# print-width wrap did for that specific edit. A stricter variant that skips reflowing any
# text that already has short natural lines (e.g. PDF's own wrap) eliminates those 3
# regressions entirely, but was rejected: it also skips reflowing away the PDF/HTML
# cross-pipeline mismatch, leaving the mean text/html ratio at 0.975 -- effectively no
# improvement at all for the actual problem this ticket exists to fix.
_WA_GLOBAL_NOISE_PATTERNS = [
    # Tracking code, e.g. "Z-0056.2", "H-0530.1", "S-2500.1" -- generalized from the ticket's
    # own [ZH] class, which misses the real "S-" prefix (1,795 occurrences in the live archive).
    # Safe to strip anywhere: this shape never occurs in real bill body text, and a tracking
    # code can legitimately recur on a later page of a multi-page document.
    re.compile(r"[A-Z]-\d{4}\.\d+"),
    # Page-number + bill-ID watermark, e.g. "p. 1    HB 1337" -- per the ticket's watermark
    # Known Gap. Requires a trailing bill-ID so it can't collide with a real legal citation
    # like "F. Supp. 312" (confirmed: 13 real "p. N" occurrences in the live archive, all
    # citations, none matching this stricter shape). Also global: a watermark repeats on every
    # page, not just the first.
    re.compile(r"p\.\s*\d+\s+[HS]B\s*\d+", re.IGNORECASE),
]

# Everything below is boilerplate that only ever legitimately appears in the document's leading
# header block (title/legislature/session/vote-tally/sponsor/procedural lines), never restated
# in the substantive body -- unlike the patterns above, several of these are ordinary English
# phrase shapes ("Committee on <x>.", "<year> regular session", "House Bill <n>") that real bill
# TEXT can and does legitimately contain when it creates a committee, cites a session, or amends
# another numbered bill. Applying them across the whole document (as an earlier version of this
# cleaner did) silently deleted that real content -- confirmed against constructed body text
# such as "The joint committee on veterans affairs shall report..." and "amends Senate Bill 5129"
# both losing their real subject matter. _clean_wa_text() therefore only ever runs this list
# against the text preceding the bill's own enacting clause ("AN ACT ..."), which every real and
# fixture WA document places immediately after this boilerplate -- see _WA_ENACTING_CLAUSE.
_WA_HEADER_ONLY_NOISE_PATTERNS = [
    # Bill title with any optional prefix phrase (SUBSTITUTE, SECOND SUBSTITUTE, ENGROSSED
    # SUBSTITUTE, "CERTIFICATION OF ENROLLMENT", etc.) before "(SENATE|HOUSE) BILL <n>" -- the
    # general shape, not an enumerated prefix list, per the ticket's title-prefix Known Gap.
    # "\d{3,4}" (not "\d+"): a real enrolled-bill header glues the bill number directly onto
    # the following ordinal-legislature number with no separator ("...BILL 101469TH
    # LEGISLATURE...") -- an unbounded "\d+" swallows the legislature's ordinal digits too.
    re.compile(r"(?:[A-Z][A-Z ]*\s+)?(?:SENATE|HOUSE) BILL \d{3,4}", re.IGNORECASE),
    # "\d{1,3}(?:st|nd|rd|th)" (not "\d+\w*"): the ordinal-suffix form is not just more
    # specific, it avoids catastrophic backtracking -- an unanchored "\d+\w*" tries every
    # digit run in the whole document (statute citations, dollar amounts, dates, ...) at every
    # possible split between its two quantifiers before failing, which measured >120s on a
    # single real ~90KB "Passed Legislature" bill. The ordinal-suffix form has no such
    # ambiguous split and resolves in milliseconds on the same real document.
    re.compile(
        r"State of Washington\s*\d{1,3}(?:st|nd|rd|th)\s*Legislature\s*\d{4}[^.]{0,40}?Session",
        re.IGNORECASE,
    ),
    # An enrolled bill's own ordinal-legislature/session line has no "State of Washington"
    # prefix at all, e.g. "69TH LEGISLATURE2025 REGULAR SESSION" -- found on the same real
    # "Passed Legislature" document as the digit-boundary bug above. Known minor imprecision:
    # when the bill number is glued directly onto this with no separator at all (e.g. "...BILL
    # 101469TH LEGISLATURE..."), there is no reliable way to tell where a variable-length bill
    # number ends and a variable-length ordinal begins from the digits alone -- this can nibble
    # the bill number's own last digit along with the real ordinal-legislature match. Accepted
    # as a documented, narrow gap (see the PR description) rather than chased further: it only
    # affects the cosmetic bill-number digits inside boilerplate that's being removed anyway.
    re.compile(
        r"\d{1,3}(?:st|nd|rd|th)\s*Legislature\s*\d{4}[^.]{0,40}?Session", re.IGNORECASE
    ),
    # The enrolled-bill signing/vote-tally block, e.g. "Passed by the House March 11, 2025
    # Yeas 93 Nays 3Speaker of the House of RepresentativesPassed by the Senate April 16,
    # 2025 Yeas 48 Nays 1President of the Senate" -- printed once per chamber on every real
    # "Passed Legislature"/enrolled document regardless of the actual vote count or date.
    re.compile(
        r"Passed by the (?:House|Senate)[^.]*?Yeas\s*\d+\s*Nays\s*\d+"
        r"(?:Speaker|President) of the (?:House(?: of Representatives)?|Senate)",
        re.IGNORECASE,
    ),
    # Found against a real "Substitute Passed Legislature" PDF during AC6 refinement: a
    # page-break can split the session line across pages, leaking a bare "<year> Regular
    # Session" (or a tail-only "Regular Session") fragment onto a numbered line that survives
    # extract_line_numbered_pdf's own line-number-anchored filtering upstream -- the full
    # pattern above only matches when the whole line lands intact on one page.
    # No trailing "\b": real data (e.g. a "Passed Legislature" enrolled-bill header) glues
    # "SESSION" directly onto the next word with no separator at all ("SESSIONPassed"), and
    # "\b" can never match between two word characters.
    re.compile(r"(?:\d{4}\s+)?(?:Regular|Special)\s+Session", re.IGNORECASE),
    # Alternate committee sponsor line for substitute stages, e.g. "By House Finance
    # (originally sponsored by Representatives ...)" -- generalized to any committee text, per
    # the ticket's alternate-sponsor-line Known Gap (never enumerate committee names).
    re.compile(r"By\s*[^()]{0,80}?\(originally sponsored by[^)]*\)", re.IGNORECASE),
    # Plain introduced-version sponsor line. Stops at the first period (real sponsor-name
    # lists never contain one), then optionally consumes exactly that one period -- real WA
    # data sometimes glues the sponsor line directly onto the following procedural line with
    # no separating space at all (e.g. "...Ramel.Prefiled 02/11/25."), so the match must be
    # able to cross that single period to reach the lookahead. Deliberately has no "$"
    # fallback in the lookahead: an open-ended ".*?" (or a lookahead that can fall back to
    # end-of-string) would swallow the entire rest of the document whenever a version simply
    # doesn't restate "Prefiled"/"Read first time" -- under-matching here is far safer than
    # that.
    re.compile(
        r"By\s*(?:Senators?|Representatives?)[^.]*?\.?"
        r"(?=\s*(?:Prefiled|Read first time|READ FIRST TIME))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:Prefiled[^.]*\.)?\s*(?:READ FIRST TIME|Read first time)[^.]*\.", re.IGNORECASE),
    re.compile(r"(?:Referred to )?Committee on [^.]*\.", re.IGNORECASE),
]

_WA_WHITESPACE_RUN = re.compile(r"\s+")

# Splits on a sentence-ending ./;/: followed by (optionally, real WA data often has none at
# all) whitespace and a capital letter or open-paren -- e.g. "...Committee on Finance.AN ACT
# Relating..." (no space) must split as reliably as "...Finance. AN ACT...". A decimal-style
# citation like "RCW 64.32.250" is never split: the digit right after the period fails the
# "capital letter or open-paren" lookahead on its own, with no extra lookbehind needed.
_WA_SENTENCE_BREAK = re.compile(r"(?<=[.;:])\s*(?=[A-Z(])")

_WA_WRAP_WIDTH = 55

# Marks the end of the boilerplate header and the start of a WA bill's own substantive text --
# every real and fixture document in this ticket's research places its enacting clause
# ("AN ACT Relating to ...") immediately after the title/legislature/sponsor/procedural block.
# If it's ever absent (e.g. a resolution with no enacting clause), header-only patterns run
# against the whole text, same as before this boundary existed -- no worse than the prior
# global behavior for that case.
_WA_ENACTING_CLAUSE = re.compile(r"\bAN ACT\b", re.IGNORECASE)


def _clean_wa_text(text: str) -> str:
    """
    Washington-only text cleaner applied to prior_text/raw_text immediately before the
    difflib.unified_diff() call in archive_bill_versions() -- see the block comment above for
    the real-data findings this encodes and the AC3/AC4 measurements behind the design.
    """
    text = _WA_WHITESPACE_RUN.sub(" ", text).strip()

    boundary_match = _WA_ENACTING_CLAUSE.search(text)
    boundary = boundary_match.start() if boundary_match else len(text)
    header, body = text[:boundary], text[boundary:]
    for pattern in _WA_HEADER_ONLY_NOISE_PATTERNS:
        header = pattern.sub("", header)
    text = header + body

    for pattern in _WA_GLOBAL_NOISE_PATTERNS:
        text = pattern.sub("", text)
    text = _WA_WHITESPACE_RUN.sub(" ", text).strip()
    lines = []
    for sentence in _WA_SENTENCE_BREAK.split(text):
        lines.extend(textwrap.wrap(sentence, width=_WA_WRAP_WIDTH) or [""])
    return "\n".join(lines)


# OPEN-11: Michigan-only text cleaning applied to prior_text/raw_text immediately before the
# difflib.unified_diff() call in archive_bill_versions() (never touching the stored raw_text
# field, and never applied to any other jurisdiction).
#
# AC2 characterization, revised 2026-08-14 after a first submission (PR #20) was independently
# validated against the real archive and found to have near-zero real effect (see the ticket's
# comment thread for the full numbers). That submission's front-matter-only design was built on
# a stale premise: Michigan's application/pdf extractor was 100% broken when the ticket was last
# researched (2026-07-30), so every real diff at that time was necessarily text/html vs
# text/html. OPEN-49 (merged 2026-08-09) fixed the PDF extractor, and archive_bill_versions()'s
# real prior_text selection (text/xml or application/pdf or first-available) now prefers PDF for
# nearly every Michigan version -- which changes what actually needs cleaning:
#
# 1. Front matter/tracking-block boilerplate (the original finding) is still real for
#    text/html documents and the enacted "Public Act" stage's PDF (confirmed via a real diff
#    read, HB4493 As Passed by the House -> Public Act) -- see _MI_ENACTING_CLAUSE_RE. This is
#    stripped for every media type, safely, since resolutions simply never contain the anchor
#    and are returned unchanged by this step (AC5).
# 2. Two previously-undiscovered real noise patterns, found while independently re-validating
#    PR #20 against the full current archive (4569 real transitions) and tracing its 10 found
#    regressions to root cause:
#    (a) A per-page tracking-code/hash footer bleeds through MI's own numbered-PDF extractor at
#        every page break, not just the final page, e.g. "GSS   H05157'25_HB5314_INTR_1
#        y5icbv\x0c   2" mid-document and "Final Page\n    KHS    H00127'25_HB4010_INTR_1
#        ft61ok\x0c" at the true end -- a unique, unrepeated hash per file, so it always looks
#        like a real content change between any two versions. Enacted "Public Act" PDFs use a
#        different, plain "(N)\x0c" page-number-only footer instead (no tracking hash). Both are
#        stripped unconditionally (_MI_TRACKING_PAGE_BREAK_RE, _MI_PLAIN_PAGE_NUM_RE) -- neither
#        pattern can plausibly appear in real bill text.
#    (b) MI's numbered-PDF extraction keeps each printed margin line-number as literal leading
#        text on its own line (e.g. "1         Sec. 1. ..."), and the column-padding whitespace
#        after that number is not stable between two renderings of the *same* content (observed:
#        "1         Sec." in one PDF vs. "1          Sec." -- one extra space -- in another PDF
#        of the identical bill stage). On a short bill this single incidental whitespace/line-
#        number difference can dominate the whole diff. Both are collapsed for Bill-classified
#        notes only (_MI_LEADING_LINE_NUM_RE, then per-line inner-whitespace normalization) --
#        deliberately NOT applied to Resolutions, whose own indentation/whitespace conventions
#        this distorted when tested unconditionally (see point 4).
# 3. The real remaining noise source once (1) and (2) are handled is a genuine cross-pipeline
#    line-wrap mismatch: a fixed-width-wrapped numbered PDF (~56-65 char lines) diffed against a
#    same-version-but-different-media-type document (raw HTML, or an enacted-stage PDF that uses
#    a noticeably wider print column) shares no real line boundaries at all, so difflib's
#    line-based ratio can't reflect boilerplate removal -- this is the exact class of problem
#    WA's OPEN-7 ticket solved with a sentence-reflow step. Applying the same technique
#    (_reflow_michigan_text, width tuned to MI's own ~56-65 char PDF convention rather than
#    reusing WA's constant) only when the prior and current media types genuinely differ
#    collapses a real, confirmed example (SB 542, Substitute (H-2) - 4 PDF -> As Passed by the
#    Senate HTML) from ratio 0.970 to 0.019, and the ticket's own central target case (SB 542,
#    Senate Concurred Bill -> Public Act, both PDF but different print widths) from 0.990 to
#    0.103.
# 4. PR #20's own AC2 write-up said a reflow step was tested and "made the ratio uniformly worse
#    on every transition tested" -- reproduced here, but only for two specific cases, not as a
#    blanket verdict: (a) applying reflow to *same-media-type* pairs that were already
#    line-aligned coarsens line granularity for no benefit (confirmed: applying reflow
#    unconditionally introduced 243 real regressions across the full archive, vs. 1 when gated
#    to cross-media-type pairs only); (b) applying it to Resolutions specifically is actively
#    harmful (confirmed: gating on `bill.classification == ["bill"]` removed ~90 further
#    regressions, all "Senate/House Enrolled -> Adopted Resolution" text/html pairs) -- Michigan
#    resolutions have no enacting clause and their own distinct whitespace/indentation
#    conventions this cleaner was never designed for. Gating reflow on BOTH conditions (a
#    genuine media-type change AND a Bill, not a Resolution) keeps the real win from point 3
#    while avoiding both failure modes.
# 5. Validated against a real, representative 30-bill/107-transition sample (>=15 per AC3,
#    including both of this ticket's own named bills, SB 542/HB 4493) AND the entire current MI
#    archive (1465 bills, 4569 real consecutive transitions via _version_sort_key(), 216
#    tied-stage pairs excluded per point 6 below): among the 1455 transitions that actually
#    carried some form of the noise above, 64.8% improved (64.1% meaningfully, >=20% relative or
#    >=0.05 absolute) -- a clear majority, per AC4. The remaining 3025 transitions (already-clean
#    PDF-vs-PDF pairs with nothing to strip) are confirmed byte-for-byte ratio-unchanged, as
#    expected. Exactly 1 regression survives across the whole archive (0.964 -> 0.982, on an "As
#    Passed by the House" -> "Public Act" PDF pair that already sat at 0.964 raw -- i.e. an
#    already near-total rewrite with no real signal to preserve either way) -- a real, known,
#    accepted gap (enacted-stage PDFs occasionally use a wider print column than earlier
#    same-media-type stages, which this cleaner does not separately detect), left documented
#    rather than chased further, per AC6's "stop after 5 iterations, document the rest" bar (this
#    revision went through 7 rounds of real-data-driven refinement to get here).
# 6. Known, documented, OUT-OF-SCOPE gap (not this ticket's to fix, carried over unchanged):
#    _note_stage() maps both "Public Act" and "Senate|House Concurred Bill" to the same
#    _STAGE_ENACTED bucket with no ordinal distinguishing them (nor same-ordinal Senate vs. House
#    Substitute pairs) -- so _version_sort_key() cannot always tell which of a tied pair is truly
#    "prior". These pairs are excluded from this ticket's own AC3/AC4 validation (their true
#    chronological order isn't reliably known) -- this is OPEN-34-shaped work, not a
#    text-cleaning problem.
_MI_ENACTING_CLAUSE_RE = re.compile(
    r"the\s+people\s+of\s+the\s+state\s+of\s+michigan\s+enact\s*:", re.IGNORECASE
)
# Per-page tracking-code/hash footer, mid-document or at the true end, e.g.
#   "GSS   H05157'25_HB5314_INTR_1   y5icbv\x0c   2"
#   "Final Page\n    KHS    H00127'25_HB4010_INTR_1    ft61ok\x0c"
# A unique per-file hash, so it always looks like real content changed between any two versions.
_MI_TRACKING_PAGE_BREAK_RE = re.compile(
    r"\s*(?:Final Page\s*\n)?\s*[A-Za-z]{2,4}\s+\S*'\S*_\S+_\d+\s+[a-z0-9]{4,8}\s*\x0c\s*\d*",
    re.IGNORECASE,
)
# Enacted "Public Act" PDFs use a plain parenthesized page-number footer instead, e.g.
# "...(12)\x0c    (b) Add taxes..." -- no tracking hash, different shape from the above.
_MI_PLAIN_PAGE_NUM_RE = re.compile(r"\s*\(\d+\)\s*\x0c\s*")
# A printed margin line-number kept as literal leading text on its own line by MI's numbered-PDF
# extractor, e.g. "1         Sec. 1. ...". Bill-only (see point 2b/4 above).
_MI_LEADING_LINE_NUM_RE = re.compile(r"^\d{1,3}\s+(?=[A-Za-z(])")
_MI_INLINE_WS_RUN = re.compile(r"[ \t]+")
_MI_WHITESPACE_RUN = re.compile(r"\s+")
_MI_SENTENCE_BREAK = re.compile(r"(?<=[.;:])\s*(?=[A-Z(])")
_MI_WRAP_WIDTH = 65


def _strip_michigan_boilerplate(text: str) -> str:
    """
    Strip the boilerplate that's safe to remove regardless of note type (Bill or Resolution) --
    the enacting-clause front matter and the two page-break footer shapes. See point 1/2a above.
    """
    match = _MI_ENACTING_CLAUSE_RE.search(text)
    if match:
        text = text[match.end() :]
    text = _MI_TRACKING_PAGE_BREAK_RE.sub(" ", text)
    text = _MI_PLAIN_PAGE_NUM_RE.sub(" ", text)
    return text


def _reflow_michigan_text(text: str) -> str:
    """Collapse to one content-derived line shape per sentence (see point 3 above)."""
    text = _MI_WHITESPACE_RUN.sub(" ", text).strip()
    lines: typing.List[str] = []
    for sentence in _MI_SENTENCE_BREAK.split(text):
        lines.extend(textwrap.wrap(sentence, width=_MI_WRAP_WIDTH) or [""])
    return "\n".join(lines)


def _clean_michigan_text(
    prior_text: str,
    raw_text: str,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
    is_bill: bool,
) -> typing.Tuple[str, str]:
    """
    Clean a Michigan prior_text/raw_text pair immediately before diffing (see the AC2 comment
    above for the full real-data writeup behind this design). Boilerplate stripping is safe for
    every note type; the line-number/whitespace normalization and cross-media reflow are gated
    to Bill-classified notes only (point 2b/4 above) since Resolutions have their own, different
    conventions these steps would otherwise distort.
    """
    prior_text = _strip_michigan_boilerplate(prior_text)
    raw_text = _strip_michigan_boilerplate(raw_text)
    if is_bill:
        prior_text = "\n".join(
            _MI_INLINE_WS_RUN.sub(" ", _MI_LEADING_LINE_NUM_RE.sub("", line)).strip()
            for line in prior_text.splitlines()
        )
        raw_text = "\n".join(
            _MI_INLINE_WS_RUN.sub(" ", _MI_LEADING_LINE_NUM_RE.sub("", line)).strip()
            for line in raw_text.splitlines()
        )
        if prior_media_type != cur_media_type:
            prior_text = _reflow_michigan_text(prior_text)
            raw_text = _reflow_michigan_text(raw_text)
    return prior_text, raw_text


def archive_bill_versions(bill: typing.Any) -> dict[str, int]:
    """
    Fetch and permanently archive every not-yet-captured version+document of a bill
    (PLAN-bill-document-provenance.md, Phase 1).

    Unlike update_bill() (above), which only looks at a bill's single latest version, this
    walks every version and every document link, and skips only the exact
    (version_note, version_date, source_url) combination already archived — a natural key, not
    the BillVersion/BillVersionLink row ids, which get deleted and recreated with new ids every
    time a bill's version list changes at all (see the plan for why those ids aren't a stable
    identity to check against).

    Also computes `diff_from_previous_version` (added 2026-07-20): versions are walked in
    `_version_sort_key()` order (OPEN-34 — bill.versions.all() has no reliable order of its
    own; see the comment above that function for the audit behind this), and `prior_text`
    tracks the most recently seen version's representative text (preferring a PDF document
    over other media types when a version has more than one file — the same PDF > HTML
    priority already used elsewhere in this plan for lineage-field caching), updated once per
    version rather than once per document so that two files of the *same* version (e.g. a PDF
    and an HTML copy) never get diffed against each other. Every newly-archived document
    within a version is diffed against that same `prior_text` snapshot. Already-archived
    (skipped) documents still feed `prior_text` so a partial re-run (e.g. only a new
    amendment's version is unarchived) diffs correctly against previously-archived text. A
    version whose note doesn't match any known stage (_STAGE_UNKNOWN) never updates or reads
    `prior_text` at all — its documents always get `diff_from_previous_version=None` rather
    than risk placing an unrecognized version at the wrong point in the lineage.
    """
    from openstates.data.models import BillVersionDocument

    counters = {
        "fetched": 0,
        "skipped": 0,
        "fetch_errors": 0,
        "blocked": 0,
        "extract_errors": 0,
        "archived": 0,
        "conflicts": 0,
        "s3_verified": 0,
        "s3_unverified": 0,
    }

    # OPEN-11: gates _clean_michigan_text() below -- every other jurisdiction's prior_text/
    # raw_text reach difflib.unified_diff() completely untouched (AC1). is_michigan_bill also
    # requires classification == ["bill"] since several of _clean_michigan_text()'s steps are
    # deliberately not applied to Resolutions (see that function's docstring).
    is_michigan = bill.legislative_session.jurisdiction.name == "Michigan"
    is_michigan_bill = is_michigan and bill.classification == ["bill"]

    prior_text: typing.Optional[str] = None
    prior_media_type: typing.Optional[str] = None

    ordered_versions = sorted(
        bill.versions.all(), key=lambda v: _version_sort_key(v.note, v.date)
    )
    for version in ordered_versions:
        is_unknown_position = _note_stage(version.note)[0] == _STAGE_UNKNOWN
        this_version_texts: dict[str, str] = {}

        for link in version.links.all():
            existing = BillVersionDocument.objects.filter(
                bill=bill,
                version_note=version.note,
                version_date=version.date,
                source_url=link.url,
            ).first()
            if existing:
                counters["skipped"] += 1
                if not existing.is_error and existing.raw_text:
                    this_version_texts[existing.media_type] = existing.raw_text
                continue

            metadata: Metadata = {
                "url": link.url,
                "media_type": link.media_type,
                "title": bill.title,
                "jurisdiction_id": bill.legislative_session.jurisdiction_id,
            }
            func = get_extract_func(metadata)
            if func == DoNotDownload:
                continue

            try:
                data = _fetch_bytes(link.url)
            except WafBlockDetected as e:
                click.secho(
                    f"blocked (WAF) fetching {link.url} even after cookie re-warm: {e}",
                    fg="red",
                )
                counters["blocked"] += 1
                continue
            except Exception as e:
                click.secho(f"failed to fetch {link.url}: {e}", fg="yellow")
                counters["fetch_errors"] += 1
                continue

            block_reason = _block_page_reason(data, link.media_type)
            if block_reason:
                click.secho(
                    f"blocked response for {link.url}: {block_reason}", fg="red"
                )
                counters["blocked"] += 1
                continue

            counters["fetched"] += 1
            sha256_hash = hashlib.sha256(data).hexdigest()
            local_md5 = hashlib.md5(data).hexdigest()

            ext = MIMETYPES.get(link.media_type, "bin")
            path = _archive_path(bill, version.note, version.date, link.url, ext)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
            except OSError as e:
                click.secho(f"failed to persist {link.url} to {path}: {e}", fg="red")
                continue

            object_key = _s3_object_key(path)
            archive_location = _upload_and_verify(path, object_key, local_md5)
            archived_at = timezone.now() if archive_location else None
            if archive_location:
                counters["s3_verified"] += 1
            else:
                counters["s3_unverified"] += 1

            raw_text = ""
            is_error = True
            try:
                raw_text = _cleanup(func(data, metadata))
                is_error = not bool(raw_text)
            except Exception as e:
                click.secho(f"exception extracting {link.url}: {e}", fg="red")
                counters["extract_errors"] += 1

            diff_from_previous_version = None
            if (
                prior_text is not None
                and not is_error
                and raw_text
                and not is_unknown_position
            ):
                diff_prior_text, diff_raw_text = prior_text, raw_text
                # OPEN-7: WA-only, applied to a local copy of each text so every other
                # jurisdiction's diff_from_previous_version stays byte-for-byte unchanged.
                if bill.legislative_session.jurisdiction.name == "Washington":
                    diff_prior_text = _clean_wa_text(diff_prior_text)
                    diff_raw_text = _clean_wa_text(diff_raw_text)
                if is_michigan:
                    diff_prior_text, diff_raw_text = _clean_michigan_text(
                        diff_prior_text,
                        diff_raw_text,
                        prior_media_type,
                        link.media_type,
                        is_michigan_bill,
                    )
                diff_from_previous_version = "\n".join(
                    difflib.unified_diff(
                        diff_prior_text.splitlines(),
                        diff_raw_text.splitlines(),
                        lineterm="",
                    )
                )

            try:
                BillVersionDocument.objects.create(
                    bill=bill,
                    version_note=version.note,
                    version_date=version.date,
                    source_url=link.url,
                    media_type=link.media_type,
                    raw_text=raw_text,
                    is_error=is_error,
                    sha256_hash=sha256_hash,
                    diff_from_previous_version=diff_from_previous_version,
                    archive_location=archive_location,
                    archived_at=archived_at,
                )
                counters["archived"] += 1
                if not is_error and raw_text:
                    this_version_texts[link.media_type] = raw_text
            except IntegrityError:
                # Should be rare-to-never — surface loudly rather than silently drop or crash
                # the whole run (a concurrent scrape run for the same bill is the likeliest cause).
                click.secho(
                    f"WARNING natural-key conflict archiving {bill.identifier} "
                    f"{version.note} ({version.date}) {link.url}",
                    fg="red",
                )
                counters["conflicts"] += 1

        if this_version_texts and not is_unknown_position:
            # Prefer text/xml over application/pdf when both exist for the same version
            # (found 2026-08-12, US/UT are the only jurisdictions where this choice ever
            # arises today): XML has no page-break/line-wrap artifacts, making it a cleaner
            # diffing source than PDF's line-numbered extraction. Falls through to PDF, then
            # whatever else succeeded, exactly as before for every other jurisdiction.
            if "text/xml" in this_version_texts:
                prior_media_type = "text/xml"
            elif "application/pdf" in this_version_texts:
                prior_media_type = "application/pdf"
            else:
                prior_media_type = next(iter(this_version_texts))
            prior_text = this_version_texts[prior_media_type]

    return counters


@click.group()
def main() -> None:
    pass


def _resample(state: str, n: int = 50) -> None:
    """
    Grab new versions for a state from the database.
    """
    init_django()
    from openstates.data.models import BillVersion

    versions = BillVersion.objects.filter(
        bill__legislative_session__jurisdiction_id=abbr_to_jid(state)
    ).order_by("?")[:n]

    count = 0
    fieldnames = [
        "id",
        "session",
        "identifier",
        "title",
        "jurisdiction_id",
        "media_type",
        "url",
        "note",
    ]

    with open(get_raw_dir() / f"{state}.csv", "w") as outf:
        out = csv.DictWriter(outf, fieldnames=fieldnames)
        out.writeheader()
        for v in versions:
            for link in v.links.all():
                out.writerow(
                    {
                        "id": v.id,
                        "session": v.bill.legislative_session.identifier,
                        "jurisdiction_id": v.bill.legislative_session.jurisdiction_id,
                        "identifier": v.bill.identifier,
                        "title": v.bill.title,
                        "url": link.url,
                        "media_type": link.media_type,
                        "note": v.note,
                    }
                )
                count += 1
    click.secho(f"wrote new sample csv with {count} records")


@main.command(help="obtain a sample of bills to extract text from")
@click.argument("state")
@click.option("--resample/--no-resample", default=False)
@click.option("--quiet/--no-quiet", default=False)
def sample(state: str, resample: bool, quiet: bool) -> int:
    if resample:
        _resample(state)
    count = missing = empty = skipped = 0
    with open(get_raw_dir() / f"{state}.csv") as f:
        for version in csv.DictReader(f):
            count += 1
            filename, data = download(version)
            if not filename or not data:
                missing += 1
                continue
            text_filename, n_bytes = extract_to_file(
                filename, data, typing.cast(Metadata, version)
            )
            if text_filename == DoNotDownload:
                skipped += 1
            elif not n_bytes:
                empty += 1
            if not quiet:
                click.secho(f"{filename} => {text_filename} ({n_bytes} bytes)")
    # decide and print result
    status = "green"
    if empty or missing:  # arbitrary threshold for now
        status = "red"
    click.secho(
        f"{state}: processed {count}, {skipped} skipped, {missing} missing, {empty} empty",
        fg=status,
    )
    if status == "red":
        return 1
    return 0


@main.command(help="run sample on all states, used for CI")
@click.pass_context
def test(ctx: typing.Any) -> None:
    failures = 0
    states = sorted(CONVERSION_FUNCTIONS.keys())
    click.secho(f"testing {len(states)} states...", fg="white")
    for state in states:
        failures += ctx.invoke(sample, state=state, quiet=True)
    sys.exit(failures)


@main.command(help="print a status table showing the current condition of states")
def status() -> None:
    init_django()
    from openstates.data.models import Bill

    states = sorted(CONVERSION_FUNCTIONS.keys())
    click.secho("state |  bills  | missing | errors ", fg="white")
    click.secho("===================================", fg="white")
    for state in states:
        all_bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state)
        )
        missing_search = all_bills.filter(searchable__isnull=True).count()
        errors = all_bills.filter(searchable__is_error=True).count()
        all_bills = all_bills.count()

        errcolor = mscolor = "green"
        if missing_search > 0:
            missing_search = math.ceil(missing_search / all_bills * 100)
            mscolor = "yellow"
        if missing_search > 1:
            mscolor = "red"
        if errors > 0:
            errcolor = "yellow"
            errors = math.ceil(errors / all_bills * 100)
        if errors > 5:
            errcolor = "red"

        click.echo(
            f"{state:5} | {all_bills:7} | "
            + click.style(f"{missing_search:6}%", fg=mscolor)
            + " | "
            + click.style(f"{errors:6}%", fg=errcolor)
        )


@main.command(help="rebuild the search index objects for a given state")
@click.argument("state")
@click.option("--session", default=None)
def reindex_state(state: str, session: str = None) -> None:
    init_django()
    from openstates.data.models import SearchableBill

    if session:
        bills = SearchableBill.objects.filter(
            bill__legislative_session__jurisdiction_id=abbr_to_jid(state),
            bill__legislative_session__identifier=session,
        )
    else:
        bills = SearchableBill.objects.filter(
            bill__legislative_session__jurisdiction_id=abbr_to_jid(state)
        )

    ids = list(bills.values_list("id", flat=True))
    print(f"reindexing {len(ids)} bills for state")
    reindex(ids)


@main.command(help="update the saved bill text in the database")
@click.argument("state")
@click.option("-n", default=None)
@click.option("--clear-errors/--no-clear-errors", default=False)
@click.option("--checkpoint", default=500)
@click.option("--session", default=None)
def update(
    state: str, n: int, clear_errors: bool, checkpoint: int, session: str = None
) -> None:
    init_django()
    from openstates.data.models import Bill, SearchableBill

    # print status within checkpoints
    status_num = checkpoint / 5

    stats.write_stats(
        [
            {
                "metric": "text_extraction_runs",
                "fields": {"total": 1},
                "tags": {"jurisdiction": state},
            }
        ]
    )

    if state == "all":
        all_bills = Bill.objects.all()
    elif session:
        all_bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state),
            legislative_session__identifier=session,
        )
    else:
        all_bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state)
        )

    if clear_errors:
        if state == "all":
            print("--clear-errors only works with specific states, not all")
            return
        errs = SearchableBill.objects.filter(bill__in=all_bills, is_error=True)
        print(f"clearing {len(errs)} errors")
        errs.delete()

    missing_search = all_bills.filter(searchable__isnull=True)
    if state == "all":
        MAX_UPDATE = 1000
        aggregates = missing_search.values(
            "legislative_session__jurisdiction__name"
        ).annotate(count=Count("id"))
        for agg in aggregates:
            state_name = agg["legislative_session__jurisdiction__name"]
            if agg["count"] > MAX_UPDATE:
                click.secho(
                    f"Too many bills to update for {state_name}: {agg['count']}, skipping",
                    fg="red",
                )
                missing_search = missing_search.exclude(
                    legislative_session__jurisdiction__name=state_name
                )
        print(f"{len(missing_search)} missing, updating")
    else:
        print(
            f"{state}: {len(all_bills)} bills, {len(missing_search)} without search results"
        )
    stats.write_stats(
        [
            {
                "metric": "text_extraction_missing",
                "fields": {"vectors": len(missing_search)},
                "tags": {"jurisdiction": state},
            }
        ]
    )

    if n:
        missing_search = missing_search[: int(n)]
    else:
        n = len(missing_search)

    ids_to_update = []
    updated_count = 0

    # going to manage our own transactions here so we can save in chunks
    transaction.set_autocommit(False)

    for b in missing_search:
        ids_to_update.append(update_bill(b))
        updated_count += 1
        if updated_count % status_num == 0:
            print(f"{state}: updated {updated_count} out of {n}")
        if updated_count % checkpoint == 0:
            reindex(ids_to_update)
            transaction.commit()
            ids_to_update = []

    stats.write_stats(
        [
            {
                "metric": "text_extraction",
                "fields": {"updates": len(ids_to_update)},
                "tags": {"jurisdiction": state},
            }
        ]
    )
    # be sure to reindex final set
    reindex(ids_to_update)
    transaction.commit()
    transaction.set_autocommit(True)
    stats.write_stats(
        [
            {
                "metric": "last_text_extract",
                "fields": {"time": int(time.time())},
                "tags": {"jurisdiction": state},
            }
        ]
    )
    stats.close()


@main.command(
    help="permanently archive every not-yet-captured bill version + document "
    "(PLAN-bill-document-provenance.md, Phase 1)"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option("-n", default=None, help="limit number of bills processed, for testing")
def archive(state: str, session: str = None, n: int = None) -> None:
    init_django()
    from openstates.data.models import Bill

    if state == "all":
        bills = Bill.objects.all()
    elif session:
        bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state),
            legislative_session__identifier=session,
        )
    else:
        bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state)
        )

    bills = bills.prefetch_related("versions__links")
    if n:
        bills = bills[: int(n)]

    totals = {
        "fetched": 0,
        "skipped": 0,
        "fetch_errors": 0,
        "blocked": 0,
        "extract_errors": 0,
        "archived": 0,
        "conflicts": 0,
        "s3_verified": 0,
        "s3_unverified": 0,
    }
    bill_count = 0
    for bill in bills:
        bill_count += 1
        for key, value in archive_bill_versions(bill).items():
            totals[key] += value

    status_color = "green"
    if totals["conflicts"]:
        status_color = "red"
    elif (
        totals["fetch_errors"]
        or totals["blocked"]
        or totals["extract_errors"]
        or totals["s3_unverified"]
    ):
        status_color = "yellow"

    click.secho(
        f"{state}: {bill_count} bills checked | "
        f"fetched={totals['fetched']} skipped={totals['skipped']} "
        f"archived={totals['archived']} fetch_errors={totals['fetch_errors']} "
        f"blocked={totals['blocked']} "
        f"extract_errors={totals['extract_errors']} conflicts={totals['conflicts']} "
        f"s3_verified={totals['s3_verified']} s3_unverified={totals['s3_unverified']}",
        fg=status_color,
    )
    if totals["conflicts"]:
        # A conflict means our own uniqueness assumption was wrong somewhere — worth a
        # non-zero exit so this surfaces as a failure in run-scrape.sh, not just a log line.
        sys.exit(1)


def recompute_bill_diff_order(bill: typing.Any) -> dict[str, list]:
    """
    Recompute `diff_from_previous_version` for one bill's already-archived
    `BillVersionDocument` rows using `_version_sort_key()` ordering (OPEN-34), entirely from
    already-stored `raw_text` — no re-fetching, no re-extraction, same "reprocess in place"
    approach OPEN-33 used for its VA backfill. `BillVersionDocument` has no FK to
    `BillVersion` by design (see its docstring), so rows are grouped by their own
    `(version_note, version_date)` natural key rather than joined to any live `BillVersion`.

    Returns {"unchanged": [doc, ...], "changed": [(doc, new_diff_or_None), ...]} — "changed"
    covers both correcting a wrong diff and nulling out a version whose note doesn't match any
    known stage (_STAGE_UNKNOWN), mirroring archive_bill_versions()'s own skip-diffing behavior
    for those. Callers decide whether to persist "changed" (see `recompute_diff_order` CLI
    command's --dry-run/--commit).
    """
    from openstates.data.models import BillVersionDocument

    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    groups: dict[tuple, list] = {}
    for doc in docs:
        groups.setdefault((doc.version_note, doc.version_date), []).append(doc)

    ordered_keys = sorted(groups.keys(), key=lambda k: _version_sort_key(k[0], k[1]))

    unchanged = []
    changed = []
    prior_text: typing.Optional[str] = None
    for note, date in ordered_keys:
        is_unknown_position = _note_stage(note)[0] == _STAGE_UNKNOWN
        group_texts: dict[str, str] = {}
        for doc in groups[(note, date)]:
            new_diff = None
            if (
                prior_text is not None
                and not doc.is_error
                and doc.raw_text
                and not is_unknown_position
            ):
                new_diff = "\n".join(
                    difflib.unified_diff(
                        prior_text.splitlines(), doc.raw_text.splitlines(), lineterm=""
                    )
                )
            if new_diff != doc.diff_from_previous_version:
                changed.append((doc, new_diff))
            else:
                unchanged.append(doc)
            if not doc.is_error and doc.raw_text:
                group_texts[doc.media_type] = doc.raw_text
        if group_texts and not is_unknown_position:
            prior_text = group_texts.get("application/pdf") or next(
                iter(group_texts.values())
            )

    return {"unchanged": unchanged, "changed": changed}


@main.command(
    help="recompute diff_from_previous_version for already-archived bill versions using the "
    "OPEN-34 ordering fix, instead of trusting original archive-time walk order (AC4)"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="apply corrections to the DB; default is a dry run that only reports counts",
)
def recompute_diff_order(state: str, session: str = None, commit: bool = False) -> None:
    init_django()
    from openstates.data.models import Bill

    if state == "all":
        bills = Bill.objects.all()
    elif session:
        bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state),
            legislative_session__identifier=session,
        )
    else:
        bills = Bill.objects.filter(
            legislative_session__jurisdiction_id=abbr_to_jid(state)
        )

    # Only bills with at least one archived document are worth walking -- matches this
    # command's job (correcting already-archived data), not archive()'s (fetching new data).
    bills = bills.filter(version_documents__isnull=False).distinct()

    total_unchanged = 0
    total_corrected = 0
    total_nulled = 0
    bill_count = 0

    for bill in bills:
        bill_count += 1
        result = recompute_bill_diff_order(bill)
        total_unchanged += len(result["unchanged"])
        for doc, new_diff in result["changed"]:
            if new_diff is None:
                total_nulled += 1
            else:
                total_corrected += 1
            if commit:
                doc.diff_from_previous_version = new_diff
                doc.save(update_fields=["diff_from_previous_version"])

    mode = "COMMITTED" if commit else "DRY RUN"
    click.secho(
        f"{state}: [{mode}] {bill_count} bills checked | "
        f"unchanged={total_unchanged} corrected={total_corrected} nulled={total_nulled}",
        fg="green" if commit else "yellow",
    )


def _reextract_document(doc: typing.Any) -> dict[str, typing.Any]:
    """
    Re-run text extraction for one already-archived `BillVersionDocument`, reading its raw
    bytes directly from the local archive copy on `/Volumes/DDP-HOT` -- no re-fetching from
    the live site, no S3 involvement. Same "reprocess in place" approach OPEN-33 used for its
    VA backfill, generalized here (OPEN-49) so it isn't a one-off script per jurisdiction.

    Returns a dict with keys: "attempted" (bool -- False means the local file couldn't be
    found, so the row wasn't touched at all), "new_raw_text", "new_is_error", "reason" (set on
    any non-fatal skip/failure, for the dry-run report).
    """
    from openstates import settings

    if not doc.archive_location:
        return {"attempted": False, "reason": "no archive_location on row"}

    # archive_location is an s3:// URI; _s3_object_key() made it a 1:1 mirror of the local
    # ARCHIVE_ROOT_DIR-relative path, so reversing that is just stripping the bucket prefix.
    prefix = f"s3://{S3_BILL_ARCHIVE_BUCKET}/"
    if not doc.archive_location.startswith(prefix):
        return {
            "attempted": False,
            "reason": f"unrecognized archive_location shape: {doc.archive_location}",
        }
    rel_path = doc.archive_location[len(prefix) :]
    local_path = os.path.join(settings.ARCHIVE_ROOT_DIR, rel_path)
    if not os.path.exists(local_path):
        return {"attempted": False, "reason": f"local file missing: {local_path}"}

    with open(local_path, "rb") as f:
        data = f.read()

    metadata: Metadata = {
        "url": doc.source_url,
        "media_type": doc.media_type,
        "title": doc.bill.title,
        "jurisdiction_id": doc.bill.legislative_session.jurisdiction_id,
    }
    func = get_extract_func(metadata)
    if func == DoNotDownload:
        return {"attempted": True, "reason": "DoNotDownload for this media type"}

    try:
        new_raw_text = _cleanup(func(data, metadata))
        new_is_error = not bool(new_raw_text)
    except Exception as e:
        return {
            "attempted": True,
            "new_raw_text": "",
            "new_is_error": True,
            "reason": f"extraction raised: {e}",
        }
    return {
        "attempted": True,
        "new_raw_text": new_raw_text,
        "new_is_error": new_is_error,
        "reason": None,
    }


@main.command(
    help="re-run text extraction for already-archived (but errored) bill documents, "
    "reading the already-downloaded raw file off disk -- no re-fetching, no S3 (OPEN-49)"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="apply corrections to the DB; default is a dry run that only reports counts",
)
def reextract(state: str, session: str = None, commit: bool = False) -> None:
    init_django()
    from openstates.data.models import BillVersionDocument

    docs = BillVersionDocument.objects.filter(
        bill__legislative_session__jurisdiction_id=abbr_to_jid(state), is_error=True
    )
    if session:
        docs = docs.filter(bill__legislative_session__identifier=session)
    docs = docs.select_related("bill", "bill__legislative_session")

    now_fixed = 0
    still_error = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    doc_count = 0

    for doc in docs:
        doc_count += 1
        result = _reextract_document(doc)
        if not result["attempted"]:
            skipped += 1
            skip_reasons[result["reason"]] = skip_reasons.get(result["reason"], 0) + 1
            continue
        if result.get("new_is_error"):
            still_error += 1
        else:
            now_fixed += 1
        if commit:
            doc.raw_text = result.get("new_raw_text", "")
            doc.is_error = result.get("new_is_error", True)
            doc.save(update_fields=["raw_text", "is_error", "updated_at"])

    mode = "COMMITTED" if commit else "DRY RUN"
    click.secho(
        f"{state}: [{mode}] {doc_count} errored docs checked | "
        f"now_fixed={now_fixed} still_error={still_error} skipped={skipped}",
        fg="green" if commit else "yellow",
    )
    if skip_reasons:
        for reason, count in sorted(skip_reasons.items(), key=lambda kv: -kv[1])[:10]:
            click.secho(f"  skipped ({count}x): {reason}", fg="yellow")


def reindex(ids_to_update: list[int]) -> None:
    from openstates.data.models import SearchableBill

    print(f"updating {len(ids_to_update)} search vectors")
    res = SearchableBill.objects.filter(id__in=ids_to_update).update(
        search_vector=(
            SearchVector("all_titles", weight="A", config="english")
            + SearchVector("raw_text", weight="B", config="english")
        )
    )
    print(f"updated {res}")


if __name__ == "__main__":
    main()
