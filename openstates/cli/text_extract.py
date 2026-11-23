#!/usr/bin/env python
import os
import re
import hashlib
import difflib
import typing
import sys
import csv
import datetime
import json
import logging
import math
import subprocess
import warnings
import click
import requests
import scrapelib
import time
from pathlib import Path
from types import SimpleNamespace
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
from openstates.fulltext.pa import ODT_MEDIA_TYPE, is_opendocument_text
from ..utils.instrument import Instrumentation
from ..utils.cookie_provider import (
    BLOCK_PAGE_MARKERS as _BLOCK_PAGE_MARKERS,
    WafBlockDetected,
    content_matches_block_markers,
    content_matches_fake_404_block,
)
from ..utils.resilience_profiles import profile_for_netloc
from ..utils.tls import VerifyingScraper
from ..utils.waf_circuit_breaker import raise_if_waf_block_threshold_reached
from ..utils.version_ordering import (
    STAGE_UNKNOWN as _STAGE_UNKNOWN,
    NON_BILL_AMENDMENT as _NON_BILL_AMENDMENT,
    ACTIONS_REVERSED_KEY,
    ORIGINAL_CLASSIFICATION_KEY,
    is_amendment_text as _is_amendment_text,
    is_bill_version as _is_bill_version,
    is_procedural_document as _is_procedural_document,
    non_bill_kind as _non_bill_kind,
    note_stage as _note_stage,
    origin_chamber as _origin_chamber,
    version_sort_key as _version_sort_key,
    _DATE_ORDERED_JURISDICTIONS,
)
from openstates.exceptions import ScrapeError
from openstates.ddp_states import state_rule_for_name

# OPEN-554: the per-state cleaners moved to openstates/ddp_states/<st>.py; their old names stay importable from here.
from openstates.ddp_states.al import _clean_al_text  # noqa: F401
from openstates.ddp_states.az import _reflow_paragraphs  # noqa: F401
from openstates.ddp_states.la import _clean_louisiana_text  # noqa: F401
from openstates.ddp_states.ma import _clean_massachusetts_text  # noqa: F401
from openstates.ddp_states.mi import (  # noqa: F401
    _clean_michigan_text,
    _reflow_michigan_text,
    _strip_michigan_boilerplate,
)
from openstates.ddp_states.nc import _clean_nc_text  # noqa: F401
from openstates.ddp_states.ne import _clean_ne_text  # noqa: F401
from openstates.ddp_states.pa import _clean_pa_text  # noqa: F401
from openstates.ddp_states.va import (  # noqa: F401
    _clean_virginia_text,
    _reflow_virginia_text,
    _strip_virginia_boilerplate,
)
from openstates.ddp_states.wa import _clean_wa_text  # noqa: F401

# OPEN-258: Django's root logger (openstates/settings.py) is DEBUG with propagate=True, and
# PR #40's S3 GetObject fallback (_read_bill_document_bytes) inherits it -- without this, every
# S3 call logs the full signed request, including the live credential (STS session token or a
# long-lived key, whichever this process is running as), to stdout/wherever that's captured.
# people.py already guards its own S3 calls the same way; this module had no equivalent.
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

stats = Instrumentation()
# Certificates checked per host, like the scrapers (OPEN-800: utils/tls.py).
scraper = VerifyingScraper()
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
    ODT_MEDIA_TYPE: "odt",  # OPEN-516: Pennsylvania's "DOC" printings
}

# Phase 2 (PLAN-bill-document-provenance.md): S3 Glacier Deep Archive upload + verify.
# Always go through the sudo-gated proxy wrapper -- no raw AWS credentials or CLI exist on
# this box by design (see ddp-infra/Production_S3_Wrappers.md).
S3_BILL_ARCHIVE_WRAPPER = "/Users/agentsmith/bin/ddp-prod-s3-bill-archive"
S3_BILL_ARCHIVE_BUCKET = "ddp-bill-archive"

# OPEN-237: how often archive()'s progress heartbeat fires (see its own comment at the call
# site) -- frequent enough that a stalled-run detector polling every few minutes sees it,
# infrequent enough not to flood the log on a large jurisdiction's run.
#
# Deploy ordering: this must ship before ddp-open-states's os-status starts reading the
# heartbeat line it produces -- deployed the other way round, os-status finds no heartbeat
# for any in-flight run and reports every archiver as stalled, even though nothing is
# actually wrong. Same pairing requirement as cloud_archiver.py's OPEN-238 note. Rolling
# back reverses the order: os-status's consumer change goes first, this producer change
# second (or os-status simply tolerates a run of stale results in between).
_ARCHIVE_HEARTBEAT_INTERVAL_S = 60

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
    ODT_MEDIA_TYPE: (b"PK\x03\x04",),
}
# _BLOCK_PAGE_MARKERS itself now lives in openstates.utils.cookie_provider (OPEN-19) so
# scrapers and this archiver share one block-detection heuristic instead of each keeping
# its own copy that can drift out of sync.


def _sniffed_media_type(data: bytes, media_type: str) -> str:
    """The media type the bytes really are, where the site's label is known to be wrong.

    OPEN-516: Pennsylvania serves OpenDocument text files labelled application/msword. Read
    by the label, the magic-byte check below expects an OLE .doc header and rejects every one
    as a block page, and the stored media_type would misname the file. So an msword-labelled
    file whose bytes are OpenDocument text is treated, checked and stored as OpenDocument.
    Every other label is returned unchanged."""
    if media_type == "application/msword" and is_opendocument_text(data):
        return ODT_MEDIA_TYPE
    return media_type


def _block_page_reason(data: bytes, media_type: str) -> typing.Optional[str]:
    magics = _BINARY_MAGIC_BYTES.get(media_type)
    if magics and not any(data.startswith(m) for m in magics):
        return f"expected {media_type} magic bytes, got {data[:16]!r} instead"
    sniff = data[:2048].lower()
    for marker in _BLOCK_PAGE_MARKERS:
        if marker in sniff:
            return f"content matches known block-page marker {marker!r}"
    return None


# Per-profile scrapelib.Scraper instances (OPEN-53) -- lazily created so a WAF-profiled
# jurisdiction's own rate limit doesn't affect (or get affected by) every other jurisdiction
# sharing the plain module-level `scraper` above. Module-level state is fine: each
# `os-text-extract archive` invocation is its own fresh process.
_profile_scrapers: typing.Dict[str, scrapelib.Scraper] = {}

# Per-profile consecutive-WAF-block counters (OPEN-52), same reasoning.
_profile_consecutive_blocks: typing.Dict[str, int] = {}

# OPEN-323: consecutive connect/timeout failures on the plain (non-WAF-profiled) fetch path. MA's
# archive spent 12 hours on ~1 bill per 16 minutes once malegislature.gov stopped accepting
# connections: scrapelib's 5 retries (~14 min per document) end in a ConnectTimeout that lands
# in the per-document `fetch_errors` count, and nothing ever looked at the run as a whole until
# ECS's 12-hour wait expired. Counts only connection-level failures -- an HTTP 404 on one dead
# link is routine (MI has plenty) and says nothing about the site. 5 is small on purpose: at
# MA's observed pace it is already over an hour, and fast failures reach it in seconds. WAF-
# profiled jurisdictions (MI, FL) turn ConnectionError into WafBlockDetected above and have
# their own breaker (OPEN-52), so they never reach this one.
_MAX_CONSECUTIVE_CONNECT_FAILURES = 5
_consecutive_connect_failures = 0


def _note_fetch_failure(exc: Exception, url: str) -> None:
    """Count a failed plain fetch; abort the whole run once connection failures pile up.

    Raises ScrapeError (the same abort `archive()` already turns into exit 1 for OPEN-52's WAF
    breaker, which `cloud_archiver.py` reports as a failed run) after
    `_MAX_CONSECUTIVE_CONNECT_FAILURES` in a row. A successful fetch resets the count
    (`_reset_connect_failure_count`), and so does an HTTP error response (a dead link's 404): the site
    answered, so it says nothing about connectivity. Any other exception leaves the count alone.
    """
    global _consecutive_connect_failures
    if isinstance(exc, scrapelib.HTTPError):
        _consecutive_connect_failures = 0
        return
    if not isinstance(
        exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
    ):
        return
    _consecutive_connect_failures += 1
    if _consecutive_connect_failures >= _MAX_CONSECUTIVE_CONNECT_FAILURES:
        raise ScrapeError(
            f"archive aborted: {_consecutive_connect_failures} consecutive connection "
            f"failures, last fetching {url}: {exc}"
        ) from exc


def _reset_connect_failure_count() -> None:
    global _consecutive_connect_failures
    _consecutive_connect_failures = 0


# OPEN-506: jurisdictions whose archive remembers an address the site answered 404 for (a
# `BillDocumentNotFound` row) and skips it instead of asking again on every run. Massachusetts
# lists about 20 bill PDFs (Bills/194/SD3039.pdf, HD2304.pdf, ...) that do not exist; most weeks
# malegislature.gov answers 404 at once, but on 10-16 and 11-08 it did not answer at all for
# them, and five of those no-answers in a row tripped OPEN-323's guard before any real fetch,
# so the run saved nothing. Opt-in, so no other state's behaviour changes.
_REMEMBERS_NOT_FOUND = frozenset({"Massachusetts"})
# A remembered address is asked again once this long has passed (it may get published later);
# a 404 then restarts the wait, a success deletes the row.
_NOT_FOUND_RECHECK_AFTER = datetime.timedelta(days=28)
# Set once a re-check of a remembered address gets no answer: the rest of the run skips every
# other remembered address too, so a bad day costs one ~16-minute timeout, not one per address.
_not_found_recheck_unanswered = False


def _scraper_for_profile(profile) -> scrapelib.Scraper:
    if profile.name not in _profile_scrapers:
        s = VerifyingScraper()
        s.user_agent = "Mozilla"
        # OPEN-53 (reopened 2026-08-15): retry_attempts=0 here, deliberately unlike the plain
        # module-level `scraper` above -- scrapelib.RetrySession's own retry loop has no
        # per-exception-type exclusion, so a nonzero value here would blindly retry a WAF
        # block (a rejected status, or any exception from the request itself) several times
        # with the same stale cookies before do_request()'s WafBlockDetected handling below
        # ever runs, exactly the "generic retry fires before the cookie-rewarm path engages"
        # failure mode this ticket was filed to describe. CookieProvider.fetch_with_retry's
        # own single invalidate-and-rewarm-once retry is the only retry layer for these
        # profiled fetches now, matching MI's scraper-side mi_waf_get()'s same rationale.
        s.retry_attempts = 0
        s.requests_per_minute = profile.requests_per_minute
        _profile_scrapers[profile.name] = s
    return _profile_scrapers[profile.name]


def _fetch_bytes(url: str) -> bytes:
    """
    GET url via the module-level `scraper` and return its content.

    Jurisdictions with a WAF resilience profile (OPEN-54's `resilience_profiles.py` -- MI and FL
    as of this writing) are wired to that profile's cached WAF cookies, own rate limit (OPEN-53),
    and a consecutive-block circuit breaker (OPEN-52) that aborts the whole archive run (raising
    ScrapeError, letting `archive()` exit non-zero) rather than silently absorbing every fetch as
    one more per-document `fetch_errors` count. Every other jurisdiction's fetch is unchanged.
    Deliberately scoped to this function (used by archive_bill_versions(), the path
    run-archive.sh actually calls) and not the older download()/update_bill() paths used by the
    separate `sample`/`update` commands -- out of scope for this ticket, not an oversight.
    """
    profile = profile_for_netloc(urlparse(url).netloc)
    if profile is not None:
        profile_scraper = _scraper_for_profile(profile)

        def do_request(cookies: dict, user_agent: str) -> requests.Response:
            try:
                # OPEN-23: attach the real User-Agent the cookie provider captured alongside
                # these same cookies -- sending no jurisdiction-specific User-Agent at all was
                # the original cookie/identity mismatch bug this fixed for MI.
                resp = profile_scraper.request(
                    "GET",
                    url,
                    allow_redirects=True,
                    cookies=cookies,
                    headers={"User-Agent": user_agent},
                )
            except requests.exceptions.ConnectionError as e:
                raise WafBlockDetected(str(e)) from e
            except scrapelib.HTTPError as e:
                # OPEN-53 (reopened 2026-08-15): with retry_attempts=0 above, a rejected
                # status now reaches here on the very first attempt instead of being blindly
                # retried by scrapelib itself -- a WAF can serve its block behind a genuine
                # error status (MI's fake-404, see content_matches_fake_404_block's own
                # docstring), not just a 200-status challenge page. Any other real HTTPError
                # (a genuinely dead link, a real server error) is not a WAF signature and
                # must keep propagating unchanged, same as MI's original archiver do_request.
                body = getattr(e.response, "content", None)
                if body is None:
                    body = (e.body or "").encode()
                if content_matches_fake_404_block(body) or content_matches_block_markers(
                    body
                ):
                    raise WafBlockDetected(
                        f"{e.response.status_code if e.response else '?'} response matched "
                        "known WAF block-page heuristic"
                    ) from e
                raise
            if content_matches_block_markers(resp.content):
                raise WafBlockDetected(
                    "response matched known WAF block-page heuristic"
                )
            return resp

        try:
            content = profile.cookie_provider.fetch_with_retry(do_request).content
        except WafBlockDetected as e:
            _profile_consecutive_blocks[profile.name] = (
                _profile_consecutive_blocks.get(profile.name, 0) + 1
            )
            raise_if_waf_block_threshold_reached(
                _profile_consecutive_blocks[profile.name],
                profile.circuit_breaker_max_consecutive_blocks,
                e,
                scrape_label=f"{profile.name} archive fetch",
                fetch_description=f"fetching {url}",
            )
            raise
        else:
            _profile_consecutive_blocks[profile.name] = 0
            return content

    return scraper.request("GET", url, allow_redirects=True).content


def _cleanup(text: str) -> str:
    # strip nulls
    return text.replace("\0", "")


def _has_text(text: typing.Optional[str]) -> bool:
    """True if `text` holds anything besides whitespace.

    OPEN-365: an image-only or scanned PDF extracts to whitespace (Arizona has 131 such pages,
    Michigan 1), and `bool("\n \n")` is True, so it was archived as real text: it took a
    whole-document diff, became the next version's baseline, and api-v3 offered it to LegBot ahead
    of the same version's HTML. One predicate for "has usable text", used by every path that
    decides whether a document is a baseline or a diff subject, so the archive path and the
    recompute path cannot disagree about it."""
    return bool(text) and bool(text.strip())


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


# OPEN-487: kept below the common 255-byte file-name limit with room to spare.
_MAX_ARCHIVE_FILE_NAME_BYTES = 200


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
    # OPEN-487: filesystems cap a file name at 255 bytes, and safe_note is unbounded (Nebraska's
    # AM2748 amendment note is long enough to blow it: "[Errno 36] File name too long", the
    # document fetched and then silently not saved). Only the readable note is trimmed, and only
    # when the whole name would exceed the budget, so every name that fit before is unchanged.
    # Identity never rested on the note: the url hash (full, untrimmed url) is still in the name.
    # safe_note is ASCII by construction (the regex above keeps only A-Za-z0-9_-), so its
    # characters == bytes; version_date comes from the scraper, so the fixed part is measured
    # in encoded bytes.
    fixed = len(date_part.encode("utf-8")) + len(f"-{url_hash}.{ext}")
    if fixed + len(safe_note) > _MAX_ARCHIVE_FILE_NAME_BYTES:
        safe_note = safe_note[: _MAX_ARCHIVE_FILE_NAME_BYTES - fixed].rstrip("_-") or "version"
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


def _check_etag(etag: typing.Optional[str], local_md5: str, object_key: str) -> bool:
    """Shared verification rule for both upload paths below (OPEN-192): a single-part upload's
    ETag is the plain hex MD5 of exactly the bytes S3 received, so it's a real, independent,
    server-computed check -- weaker than a full read-after-write, but the only kind available
    against Deep Archive, which has no download command at all (a real object needs a ~12hr
    restore request before it's readable). A multipart-style ETag (a "-N" suffix -- a hash of
    hashes) can't be compared this way and is treated the same as a verification failure, not
    silently accepted. Factored out so the wrapper path and OPEN-192's new direct path can't
    drift on what "verified" means -- the transport differs, this does not.
    """
    if not etag:
        click.secho(f"S3 verify failed for {object_key}: no ETag returned", fg="red")
        return False
    if "-" in etag:
        click.secho(
            f"S3 upload for {object_key} used multipart (ETag={etag}); cannot verify via "
            "ETag-as-MD5, treating as unverified",
            fg="yellow",
        )
        return False
    if etag != local_md5:
        click.secho(
            f"S3 ETag mismatch for {object_key}: local md5={local_md5} etag={etag}",
            fg="red",
        )
        return False
    return True


def _upload_and_verify(
    path: str, object_key: str, local_md5: str
) -> typing.Optional[str]:
    """
    Upload one archived document to S3 Glacier Deep Archive and verify the upload via ETag
    (PLAN-bill-document-provenance.md, Phase 2 -- verification mechanism revised 2026-07-25).

    Dispatches on `ARCHIVE_S3_MODE` (OPEN-192, Phase 3 of the scraper-execution migration):
    `"wrapper"` (default, unchanged) shells out to the sudo-gated Mac proxy, exactly as before
    this dispatch existed. `"direct"` is new: an ordinary credentialed boto3 PutObject, for
    running this same code from a cloud container that has no sudo, no wrapper binary, and no
    Mac at all -- OPEN-192's own text names this exact transport swap as what the migration
    buys ("In the cloud that upload is an ordinary credentialed PutObject and the wrapper stops
    being needed at all"). Nothing about the wrapper path below changed to make room for this;
    it is the original function, untouched, just renamed and called from here.

    Returns the s3:// URI on a verified match; None on any upload failure, ETag mismatch, or a
    multipart ETag -- the caller leaves `archive_location`/`archived_at` unset in every None
    case, so an unverified upload is never recorded as archived. Same contract, either mode.

    An explicitly-set but unrecognized `ARCHIVE_S3_MODE` value is a configuration error, not a
    signal to fall back to wrapper mode -- a typo'd value (e.g. a container env with
    `ARCHIVE_S3_MODE=driect`) would otherwise silently invoke the sudo-gated Mac wrapper, which
    doesn't exist in a cloud container, rather than failing at this one obvious point.
    """
    mode = os.environ.get("ARCHIVE_S3_MODE", "wrapper")
    if mode == "direct":
        return _upload_and_verify_direct(path, object_key, local_md5)
    if mode != "wrapper":
        click.secho(
            f"S3 upload failed for {object_key}: unrecognized ARCHIVE_S3_MODE={mode!r} "
            '(expected "wrapper" or "direct")',
            fg="red",
        )
        return None
    return _upload_and_verify_via_wrapper(path, object_key, local_md5)


def _upload_and_verify_via_wrapper(
    path: str, object_key: str, local_md5: str
) -> typing.Optional[str]:
    """The original, Mac-only upload path -- see `_upload_and_verify`'s own docstring for why
    this exists as a separate function now. Behaviour is byte-for-byte what `_upload_and_verify`
    itself used to do before OPEN-192 added a second mode; nothing here changed.

    Open assumption, not yet independently confirmed (see plan's Risk Register): that the
    proxy's `put-stream` always performs a single-part PutObject regardless of file size. Bill
    documents (PDFs/HTML) are expected to stay well under any multipart threshold.
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
    if not _check_etag(etag, local_md5, object_key):
        return None

    return f"s3://{S3_BILL_ARCHIVE_BUCKET}/{object_key}"


def _get_s3_client():
    """Isolated so tests can monkeypatch this one function rather than reaching into boto3
    itself -- mirrors cloud_collector.py's own `s3_client=None` injection convention, adapted
    to this module's plain-function (not argument-threaded) shape. Lazy import: boto3 stays an
    optional dependency for every caller that only ever runs in wrapper mode (the Mac, today)."""
    import boto3

    return boto3.client("s3")


def _upload_and_verify_direct(
    path: str, object_key: str, local_md5: str
) -> typing.Optional[str]:
    """OPEN-192's cloud upload path: an ordinary credentialed boto3 PutObject to
    `S3_BILL_ARCHIVE_BUCKET` (the same bucket the wrapper path writes), at `GLACIER_IR` instead
    of `DEEP_ARCHIVE` -- immediately readable, no ~12hr restore, which is the entire reason
    Phase 3 exists to write it (`PLAN-scraper-execution-migration.md`, "do not treat the vault
    write as sufficient").

    **Corrected 2026-08-31 (OPEN-238, Ramon's second storage-design correction): one write, not
    two.** An earlier version of this function wrote a Deep Archive copy to this bucket plus a
    second, `STANDARD`-class copy to a separate, configurable "working tier" bucket
    (`WORKING_TIER_S3_BUCKET`), requiring both to succeed. That bucket doesn't exist any more, by
    design: storage class is a property of each S3 object, not the bucket, so this single write
    is both the archive and the readable copy at once, in the same bucket the historical
    corpus already lives in. There is no bucket decision left to make and no second write to
    coordinate.

    **Corrected 2026-09-09: `STANDARD_IA` -> `GLACIER_IR`, matching the bucket's own bulk
    re-tier.** The rest of `ddp-bill-archive` (the pre-OPEN-192 Deep Archive backlog) was just
    migrated wholesale to `GLACIER_IR` rather than `STANDARD`/`STANDARD_IA` -- storage cost is
    the dominant factor for a 52+ GB and growing archive that only a small slice of gets read
    back each month (each document is synced to DDP-HOT exactly once, right after archiving,
    never again), and `GLACIER_IR`'s ~6x cheaper storage more than offsets its higher per-GB
    retrieval fee at this archive's actual read volume. Writing new documents directly at
    `STANDARD_IA` would silently reintroduce the cost mismatch this bucket-wide migration just
    fixed, one new document at a time. `GLACIER_IR` has the same "immediately readable, no
    restore" property `STANDARD_IA` had -- nothing about `_check_etag` or the read path below
    needed to change for this.

    Same verification contract as the wrapper path (`_check_etag`): a single-part PutObject's
    response ETag is the plain hex MD5 of exactly the bytes S3 stored, checked against the same
    local hash the wrapper path checks -- multipart uploads are not attempted here (bill
    documents are well under any multipart threshold, the same assumption the wrapper path
    already makes), so a `-N`-suffixed ETag is not expected in practice, but is still checked
    and still treated as unverified if one somehow appears.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client = _get_s3_client()
    except BotoCoreError as e:
        # Client construction itself can raise (e.g. ProfileNotFound from a misconfigured
        # AWS_PROFILE) even though it makes no network call -- this function's contract is
        # "None on any failure," not "None on any failure after the client exists."
        click.secho(f"S3 upload failed for {object_key}: {e}", fg="red")
        return None

    try:
        with open(path, "rb") as f:
            body = f.read()
    except OSError as e:
        click.secho(f"S3 upload failed for {object_key}: {e}", fg="red")
        return None

    try:
        client.put_object(
            Bucket=S3_BILL_ARCHIVE_BUCKET,
            Key=object_key,
            Body=body,
            StorageClass="GLACIER_IR",
        )
        head = client.head_object(Bucket=S3_BILL_ARCHIVE_BUCKET, Key=object_key)
    except (ClientError, BotoCoreError) as e:
        # BotoCoreError alongside ClientError: a credential/config/connection failure (e.g. no
        # credentials found, endpoint unreachable) isn't a ClientError at all -- it never got a
        # response from S3 to wrap -- but this function's contract is "None on any failure,"
        # not "None on any failure S3 itself reported."
        click.secho(f"S3 upload failed for {object_key}: {e}", fg="red")
        return None

    etag = head.get("ETag", "").strip('"')
    if not _check_etag(etag, local_md5, object_key):
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
# the diff lineage entirely (STAGE_UNKNOWN) rather than risking a backward diff, per the
# ticket's own framing that a wrong-direction diff is worse than a missing one.
#
# _note_stage()/_version_sort_key() (and their STAGE_* constants) moved to
# openstates/utils/version_ordering.py (OPEN-91) -- imported above as
# _note_stage/_version_sort_key/_STAGE_* so every call site below is unchanged.
# See that module's own docstring for the full OPEN-34 audit this encodes.


# OPEN-223: Massachusetts prints a bill and the law it became as two versions with two
# renderings: "Bill Text" (PDF, the bill as filed) and "Chapter Law Text (Enacted)" (HTML, the
# law as signed). Every Massachusetts bill with two documents has exactly that pair (364 bills,
# replica 2026-11-13), so with one baseline per media type (OPEN-217) the chapter law has no
# predecessor of its own type and gets no comparison at all: `recompute-diff-order ma` proposed
# nulling all 291 stored ones. Those comparisons are the only record of what changed between
# filing and signature (H 4252: four real edits), so for Massachusetts only, an HTML document with
# no HTML predecessor is compared with the previous version's PDF. _clean_massachusetts_text puts
# both renderings into one shape first. Every other jurisdiction, and every other media type,
# keeps the strict same-media baseline.
_MA_CROSS_MEDIA_BASELINE = {"text/html": "application/pdf"}


def _diff_baseline(
    prior_by_media: dict, media_type: str, jurisdiction_name: str
) -> typing.Tuple[typing.Optional[str], str]:
    """The previous text this document is compared with, and that text's media type: the
    previous version's document of the same media type (OPEN-217), or, for Massachusetts'
    enacted chapter law only, the previous version's PDF (OPEN-223, see the comment above)."""
    prior_text = prior_by_media.get(media_type)
    if prior_text is None and jurisdiction_name == "Massachusetts":
        fallback = _MA_CROSS_MEDIA_BASELINE.get(media_type)
        if fallback is not None and fallback in prior_by_media:
            return prior_by_media[fallback], fallback
    return prior_text, media_type


def apply_prediff_cleaning(
    prior_text: str,
    raw_text: str,
    *,
    jurisdiction_name: str,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """The per-jurisdiction text cleaning that runs immediately before
    `difflib.unified_diff()` -- OPEN-7 (WA), OPEN-9 (VA), OPEN-10 (AZ), OPEN-11 (MI), OPEN-371 (NC), OPEN-400 and OPEN-223 (MA), OPEN-486 (NE), OPEN-495 (PA), OPEN-496 (LA), OPEN-372 (AL).

    OPEN-554: the cleaners themselves now live in each state's file, `openstates/ddp_states/<st>.py` (`clean_text`),
    found by the state's name; this function only looks the state up.

    OPEN-219: extracted so that BOTH diff-computing paths run it. It previously lived
    inline in `archive_bill_versions` only, so `recomputed_diffs_for_documents` --
    added eight days earlier and ~600 lines away in this same file -- produced
    different diffs from identical inputs, with nothing indicating which you had.

    That mattered because the cleaning is not cosmetic. Legislative text reprints
    tracking codes, title and sponsor lines, session headers and margin line numbers
    on every version; a single inserted sentence shifts every following line number,
    so a line-based diff reports the whole remainder of the document as changed.
    OPEN-7's own commit message: "plus reflow so real edits aren't buried by
    line-number shifts."

    Measured before this fix, by classifying every diff `recompute-diff-order` would
    rewrite: Washington had already lost the cleaning from **all 4,537** of its stored
    diffs (OPEN-211's backfill ran the uncleaned path over it), and recomputing
    Virginia would have stripped it from a further **2,031** to gain 2,304 -- close to
    a one-for-one trade of real cleaning for real fixes.

    Jurisdiction is a PARAMETER rather than something read off a model, deliberately:
    `recomputed_diffs_for_documents` reads only plain attributes off each document,
    which is what lets it be tested without a database and lets `refresh-extraction`
    simulate a dry run with stand-in objects. Looking the jurisdiction up from the ORM
    here would destroy both.

    `is_bill` gates Michigan's line-number and whitespace normalisation to
    Bill-classified notes only -- Resolutions have different conventions those steps
    would distort (OPEN-11).

    The media-type pair is threaded through because `_clean_michigan_text` and
    `_clean_virginia_text` take it, but note that since OPEN-217 both call sites diff
    like-for-like, so their cross-media reflow branch is unreachable from either. It is
    left in place rather than removed: both are shared helpers with their own unit
    tests, and dropping a parameter from them is a separate cleanup. The one cross-media
    pair either path passes is Massachusetts' chapter law against its bill text (OPEN-223,
    `_diff_baseline`); `_clean_massachusetts_text` is told so, and cleans both sides alike.
    """
    # OPEN-554: each state's cleaner lives in its own file, openstates/ddp_states/<st>.py (`clean_text`); a state
    # without one gets its text unchanged, as the old if-chain gave every state it did not name.
    clean_text = state_rule_for_name(jurisdiction_name, "clean_text")
    if clean_text is None:
        return prior_text, raw_text
    return clean_text(
        prior_text,
        raw_text,
        is_bill=is_bill,
        prior_media_type=prior_media_type,
        cur_media_type=cur_media_type,
    )


def _feeds_baseline(doc: typing.Any, jurisdiction_name: str) -> bool:
    """A stored document whose text can take a diff and be the next version's baseline: extracted, with text, and
    not a line-edit paper (OPEN-564, `is_amendment_text`). The version-level exclusions (unknown stage, procedural
    note) are checked by the callers."""
    return (
        not doc.is_error
        and _has_text(doc.raw_text)
        and not _is_amendment_text(jurisdiction_name, doc.raw_text)
    )


def _version_document_order(
    rows: typing.Iterable, media_type: typing.Callable, source_url: typing.Callable
) -> list:
    """OPEN-578: the one rule for which document of a version stands for its media type, for both diff paths.

    A version's documents are walked in (media type, source_url) order and the last one of each media type that can
    be a baseline (`_feeds_baseline`) becomes that type's baseline for the next version: within a type, the document
    with the last source_url. This is the rule api-v3's `preferred_document` (api/document_choice.py, OPEN-369/533)
    applies within a type, so the bill page, search, the archiver and `recompute-diff-order` name the same document.

    `recomputed_diffs_for_documents` used to walk a version's stored rows by row id instead, so a version with two
    documents of one type (Virginia HB 854 "Finance Substitute": 1100768.PDF is row 25019, 1098528.PDF row 25023)
    took the last by row id as the baseline where the archiver took the last by URL (OPEN-578; measured on the Mac
    replica 2026-11-16 with OPEN-539's baseline comparison: Virginia 774, Arizona 358)."""
    return sorted(rows, key=lambda row: (media_type(row), source_url(row)))


def _compare_texts(
    prior_text: str,
    raw_text: str,
    *,
    jurisdiction_name: str,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> str:
    """The stored comparison of `raw_text` against `prior_text`: the jurisdiction's pre-diff cleaning
    (`apply_prediff_cleaning`, OPEN-219) on local copies, then a unified diff. One function for the archiver,
    `recompute-diff-order` and its change list (OPEN-578)."""
    diff_prior_text, diff_raw_text = apply_prediff_cleaning(
        prior_text,
        raw_text,
        jurisdiction_name=jurisdiction_name,
        is_bill=is_bill,
        prior_media_type=prior_media_type,
        cur_media_type=cur_media_type,
    )
    return "\n".join(
        difflib.unified_diff(
            diff_prior_text.splitlines(),
            diff_raw_text.splitlines(),
            lineterm="",
        )
    )


def archive_bill_versions(
    bill: typing.Any, unsaved_documents: typing.Optional[list] = None
) -> dict[str, int]:
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
    own; see the comment above that function for the audit behind this), and `prior_by_media`
    tracks one baseline **per media type** (OPEN-217), so each document is diffed against the
    previous version's document of its *own* rendering. A version's PDF is never compared
    against a previous version's XML: two extractors never agree line for line, and the result
    was a whole-document replacement rather than a changelog. Baselines are updated once per
    version rather than once per document, so two files of the *same* version never get diffed
    against each other. Already-archived (skipped) documents still feed their media type's
    baseline, so a partial re-run (e.g. only a new amendment's version is unarchived) diffs
    correctly against previously-archived text. A media type absent from one version does not
    reset its lineage — only the types present are updated. A version whose note doesn't match
    any known stage (_STAGE_UNKNOWN) never updates or reads a baseline at all — its documents
    always get `diff_from_previous_version=None` rather than risk placing an unrecognized
    version at the wrong point in the lineage. OPEN-224: a version whose note IS recognized but
    is a known short procedural document for this jurisdiction (`is_procedural_document()`) gets
    the identical treatment — see that function's own docstring for why diffing a full bill
    against one of these is worse than not diffing it at all.

    OPEN-10: for Arizona only, `prior_text`/`raw_text` are reflowed (see
    `_reflow_paragraphs()`) into local variables just before the `difflib.unified_diff()`
    call below -- word-wrap-fragment noise specific to Arizona's Word-HTML-export bill
    documents, not applicable to any other jurisdiction (Florida's own branch above is for a
    different problem -- TLS ciphers, not text cleaning). Only the text fed into
    `difflib.unified_diff()` changes; the stored `raw_text` field and the `prior_text`
    carried into the next iteration both stay the original, un-reflowed text.

    OPEN-9: for Virginia bills only, `prior_text`/`raw_text` are run through
    `_clean_virginia_text()` immediately before the `difflib.unified_diff()` call below --
    stripping repeated administrative boilerplate (session/stage markers, patron lines,
    margin artifacts, etc.) that would otherwise dominate the raw diff. This never changes
    what's stored as `raw_text` or fed into `this_version_texts`/`prior_text` tracking for the
    *next* version -- only the two local copies handed to `unified_diff()` are cleaned. No
    other jurisdiction is affected (mirrors Florida's own unrelated branch elsewhere in this
    module, and matches recompute_bill_diff_order()'s untouched, cleaning-free diff call --
    that function is OPEN-34's separate ordering-only backfill path, deliberately not in scope
    here per the ticket's no-backfill constraint).
    """
    from openstates.data.models import BillDocumentNotFound, BillVersionDocument

    global _not_found_recheck_unanswered

    counters = {
        "fetched": 0,
        "skipped": 0,
        "fetch_errors": 0,
        "blocked": 0,
        "extract_errors": 0,
        "archived": 0,
        "conflicts": 0,
        # OPEN-107: a benign lost race, counted separately from "conflicts" because the two
        # need opposite responses -- see the IntegrityError handler below.
        "concurrent_writes": 0,
        "s3_verified": 0,
        "s3_unverified": 0,
        # OPEN-263: local-persist failures used to increment no counter at all -- fetched was
        # already incremented above, but neither this nor any other counter recorded the
        # failure, so a systemic write failure (e.g. a container permission bug) looked
        # identical to "nothing new to archive" in the summary line. Confirmed live
        # 2026-09-09: every fetched document in four jurisdictions hit this on every attempt,
        # invisibly.
        "persist_errors": 0,
    }
    # OPEN-487: the documents behind persist_errors, by address and reason, so the run names
    # what it lost instead of only counting it. Not a counter (`counters` is summed as ints), so
    # the caller passes in a list to collect (url, reason) pairs.
    unsaved = unsaved_documents if unsaved_documents is not None else []

    jurisdiction_name = bill.legislative_session.jurisdiction.name
    remembers_not_found = jurisdiction_name in _REMEMBERS_NOT_FOUND  # OPEN-506
    # OPEN-11: several of _clean_michigan_text()'s steps are deliberately not applied to
    # Resolutions (see that function's docstring), so it needs to know whether this is a
    # Bill. Every other jurisdiction's prior_text/raw_text still reach
    # difflib.unified_diff() completely untouched (AC1) -- apply_prediff_cleaning() gates
    # on the jurisdiction name itself.
    #
    # OPEN-219: this is the bill's own classification, NOT ANDed with the jurisdiction as
    # it used to be. Only Michigan's cleaner reads it, so the AND was harmless -- but it
    # meant this path and recompute_bill_diff_order passed different values for the same
    # bill, an argument-parity gap of exactly the kind this ticket is about.
    is_bill = bill.classification == ["bill"]

    # OPEN-217: one baseline PER MEDIA TYPE, matching `recomputed_diffs_for_documents` below.
    #
    # This used to keep a single `prior_text` for the whole version, preferring text/xml, and
    # diff every document of the next version against it -- so a version's PDF was compared
    # against the previous version's XML. Two different extractors never agree line for line,
    # so every line read as changed and the diff collapsed to one whole-document hunk.
    #
    # Measured on the first US archive run after OPEN-211's backfill (2026-08-30): of 21 diffs
    # written per media type, 0 text/xml were whole-document and 19 of 21 application/pdf were.
    # Same bill and version -- HR 1869 "Reported in House" -- came out as "@@ -1,15 +1,23 @@"
    # for XML and "@@ -1,108 +1,156 @@" for PDF.
    #
    # Note the XML preference never caused the flattening OPEN-210 fixed; it only decided which
    # media type absorbed it. Before that ticket XML was the single-line rendering, so XML
    # looked broken; afterwards XML was correct and PDF carried the damage.
    #
    # A media type absent from one version does not reset its lineage -- only the types present
    # are updated -- so a version that ships PDF-only does not orphan the XML chain.
    prior_by_media: dict[str, str] = {}
    # OPEN-539: the id of the stored row behind each baseline text, kept beside it, so every new
    # comparison is stored with the document it was actually made against (`diff_baseline_id`).
    prior_id_by_media: dict[str, int] = {}

    ordered_versions = sorted(
        bill.versions.all(),
        key=lambda v: _version_sort_key(
            v.note,
            v.date,
            [ln.url for ln in v.links.all()],
            _origin_chamber(bill.identifier),
            jurisdiction_name,
        ),
    )
    for version in ordered_versions:
        # OPEN-224: a short procedural document (e.g. Virginia's "Governor's Recommendation",
        # a numbered "House Amendment N" excerpt in Utah) gets the same treatment as an
        # unrecognized stage -- see is_procedural_document()'s own docstring for why this
        # can't be folded into note_stage() itself.
        is_unknown_position = (
            _note_stage(version.note)[0] == _STAGE_UNKNOWN
            or _is_procedural_document(jurisdiction_name, version.note)
        )
        this_version_texts: dict[str, str] = {}
        this_version_ids: dict[str, int] = {}  # OPEN-539: the row behind each text above

        # OPEN-217 (review round 1): 3,744 production versions have more than one
        # successfully-extracted document of the SAME media type, so `this_version_texts`
        # last-write-wins had to stop depending on `links.all()`'s unspecified row order --
        # media type is the lineage key now, and that choice propagates to every later
        # comparison. Sorted by url so the same link wins on every run. OPEN-578: the order is
        # `_version_document_order`, which `recomputed_diffs_for_documents` walks too.
        for link in _version_document_order(
            version.links.all(), lambda ln: ln.media_type, lambda ln: ln.url
        ):
            existing = BillVersionDocument.objects.filter(
                bill=bill,
                version_note=version.note,
                version_date=version.date,
                source_url=link.url,
            ).first()
            # OPEN-263: a row existing is not the same as "already handled." is_error=True is
            # a confirmed extraction failure, deliberately left for OPEN-33/OPEN-229's own
            # reprocess-in-place mechanism rather than retried automatically here -- retrying
            # every is_error row on every run would repeat the exact live-traffic cost that
            # mechanism exists to avoid. But a row with is_error=False and no archive_location
            # means extraction succeeded and the S3 upload never completed (an IAM/network
            # failure at the PutObject step, found live 2026-09-09) -- that was being treated
            # as permanently done, silently, with no path back to a retry, ever.
            if existing and (existing.is_error or existing.archive_location):
                counters["skipped"] += 1
                if _feeds_baseline(existing, jurisdiction_name):
                    this_version_texts[existing.media_type] = existing.raw_text
                    this_version_ids[existing.media_type] = existing.id
                continue
            # OPEN-263 (review round 1): `existing` here, if present, is a stale row from a
            # prior run whose upload never completed -- retryable, not done. Deleting it is
            # still necessary before the INSERT below (see the comment at the create() call),
            # but doing it eagerly, here, before the fetch/persist/upload that can itself fail,
            # would throw away the stale row's own raw_text for nothing if THIS attempt also
            # fails -- turning "one stuck row" into "zero rows and no diff baseline" instead of
            # leaving the prior attempt's result in place to retry again next run. So the
            # delete is deferred to happen atomically with the create it makes room for, and
            # every early-exit below that skips the create falls back to the stale row's own
            # raw_text, matching what the skip branch above and the concurrent-write recovery
            # branch below both already do.
            if existing and _feeds_baseline(existing, jurisdiction_name):
                this_version_texts[existing.media_type] = existing.raw_text
                this_version_ids[existing.media_type] = existing.id

            metadata: Metadata = {
                "url": link.url,
                "media_type": link.media_type,
                "title": bill.title,
                "jurisdiction_id": bill.legislative_session.jurisdiction_id,
            }
            func = get_extract_func(metadata)
            if func == DoNotDownload:
                continue

            # OPEN-506: an address this site already answered 404 for is not asked again until
            # it is due for a re-check, and not at all once a re-check this run went unanswered.
            not_found = None
            if remembers_not_found:
                not_found = BillDocumentNotFound.objects.filter(
                    source_url=link.url
                ).first()
                if not_found and (
                    _not_found_recheck_unanswered
                    or timezone.now() - not_found.last_checked_at
                    < _NOT_FOUND_RECHECK_AFTER
                ):
                    click.echo(
                        f"not fetched, answered 404 before: {link.url} (last checked "
                        f"{not_found.last_checked_at:%Y-%m-%d})"
                    )
                    counters["skipped"] += 1
                    continue

            try:
                data = _fetch_bytes(link.url)
            except ScrapeError:
                # OPEN-52: a sustained, circuit-breaker-tripped WAF block must abort the whole
                # run (propagate up to archive()'s own exit-code handling), not get silently
                # absorbed as one more per-document fetch_errors/blocked count the way every
                # other exception here is.
                raise
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
                now = timezone.now()
                if (
                    remembers_not_found
                    and isinstance(e, scrapelib.HTTPError)
                    and getattr(e.response, "status_code", None) == 404
                ):
                    # OPEN-506: remember it (or restart the wait for a re-checked one).
                    row, created = BillDocumentNotFound.objects.get_or_create(
                        source_url=link.url,
                        defaults={"first_not_found_at": now, "last_checked_at": now},
                    )
                    if not created:
                        row.last_checked_at = now
                        row.save(update_fields=["last_checked_at"])
                elif not_found and isinstance(
                    e,
                    (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
                ):
                    # OPEN-506: no answer on an address already known to be missing says
                    # nothing new about the site, so it does not count toward OPEN-323's
                    # five-in-a-row stop. Wait again before the next re-check, and skip the
                    # other remembered addresses for the rest of this run.
                    not_found.last_checked_at = now
                    not_found.save(update_fields=["last_checked_at"])
                    _not_found_recheck_unanswered = True
                    continue
                _note_fetch_failure(e, link.url)  # OPEN-323: may abort the run
                continue

            if not_found:
                not_found.delete()  # OPEN-506: published after all, so archive it as usual
            _reset_connect_failure_count()
            # OPEN-516: from here on the document is handled as what its bytes are (the
            # magic-byte check, the file extension, its diff lineage and the stored row).
            media_type = _sniffed_media_type(data, link.media_type)
            block_reason = _block_page_reason(data, media_type)
            if block_reason:
                click.secho(
                    f"blocked response for {link.url}: {block_reason}", fg="red"
                )
                counters["blocked"] += 1
                continue

            counters["fetched"] += 1
            sha256_hash = hashlib.sha256(data).hexdigest()
            local_md5 = hashlib.md5(data).hexdigest()

            ext = MIMETYPES.get(media_type, "bin")
            path = _archive_path(bill, version.note, version.date, link.url, ext)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
            except OSError as e:
                click.secho(f"failed to persist {link.url} to {path}: {e}", fg="red")
                counters["persist_errors"] += 1
                unsaved.append((link.url, str(e)))
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
                is_error = not _has_text(raw_text)
            except Exception as e:
                click.secho(f"exception extracting {link.url}: {e}", fg="red")
                counters["extract_errors"] += 1

            diff_from_previous_version = None
            diff_baseline_id = None  # OPEN-539: set exactly when a comparison is made below
            # Same-media by construction (OPEN-217), so the cross-media reflow branch inside
            # _clean_michigan_text()/_clean_virginia_text() can no longer fire from this call
            # site. Left in place rather than removed: both are shared helpers with their own
            # tests, and dropping a parameter from them is a separate cleanup. The one
            # exception is Massachusetts' enacted chapter law (OPEN-223, see _diff_baseline).
            prior_text, prior_media_type = _diff_baseline(
                prior_by_media, media_type, jurisdiction_name
            )
            # OPEN-564: a line-edit paper (is_amendment_text) is treated like a procedural one, per document.
            is_amendment = _is_amendment_text(jurisdiction_name, raw_text)
            if (
                prior_text is not None
                and not is_error
                and _has_text(raw_text)
                and not is_unknown_position
                and not is_amendment
            ):
                # OPEN-219: shared with recomputed_diffs_for_documents below. Applied to
                # a local copy of each text, so the stored raw_text is untouched and every
                # jurisdiction without a cleaner is byte-for-byte unchanged.
                diff_from_previous_version = _compare_texts(
                    prior_text,
                    raw_text,
                    jurisdiction_name=jurisdiction_name,
                    is_bill=is_bill,
                    prior_media_type=prior_media_type,
                    cur_media_type=media_type,
                )
                # OPEN-539: the row whose text was just compared (the media type _diff_baseline chose).
                diff_baseline_id = prior_id_by_media[prior_media_type]

            try:
                # OPEN-107: the insert gets its own savepoint so a duplicate-key
                # IntegrityError rolls back only this statement. Without it the recovery
                # SELECT below cannot run at all -- a failed INSERT marks the connection as
                # needing rollback, and the next query raises TransactionManagementError
                # instead. Found by /pm-review, then reproduced against a real Postgres
                # unique violation rather than a hand-raised one. It also makes this
                # function safe to call from inside a caller's own transaction.atomic(),
                # which it previously was not. A savepoint per document is negligible
                # against the network fetch that precedes it.
                #
                # OPEN-263 (review round 1): `existing`'s delete (a stale, unarchived retry
                # candidate -- see the skip-check above) happens in this same atomic block,
                # immediately before the create it makes room for, not eagerly back when it
                # was first identified as retryable. That means a fetch/persist/upload
                # failure between there and here leaves the stale row untouched for the next
                # run to retry, instead of deleting it on a promise this attempt then failed
                # to keep. Doing the delete+create together also still avoids the natural-key
                # collision the delete exists for in the first place: two racing runs are
                # exactly the pre-existing IntegrityError case below, just one INSERT away
                # from the delete instead of several.
                replaced_id = existing.id if existing else None  # delete() sets existing.id to None
                with transaction.atomic():
                    if existing:
                        # OPEN-539: a comparison taken against the row being replaced no longer
                        # names a stored document; clear it (a recompute restamps it) rather
                        # than leave an id that points nowhere. Baselines never cross bills.
                        BillVersionDocument.objects.filter(
                            bill=bill, diff_baseline_id=replaced_id
                        ).update(diff_baseline_id=None)
                        existing.delete()
                    created = BillVersionDocument.objects.create(
                        bill=bill,
                        version_note=version.note,
                        version_date=version.date,
                        source_url=link.url,
                        media_type=media_type,
                        raw_text=raw_text,
                        is_error=is_error,
                        sha256_hash=sha256_hash,
                        diff_from_previous_version=diff_from_previous_version,
                        diff_baseline_id=diff_baseline_id,  # OPEN-539
                        # OPEN-492: decided once, here, from the same per-state rules the
                        # diff lineage uses; readers (api-v3) read this instead of a list.
                        # OPEN-498: some states also need the bill's title (see is_bill_version).
                        # OPEN-564: and the document's own text.
                        is_bill_version=_is_bill_version(
                            jurisdiction_name, version.note, bill.title, raw_text
                        ),
                        archive_location=archive_location,
                        archived_at=archived_at,
                    )
                counters["archived"] += 1
                if not is_error and _has_text(raw_text) and not is_amendment:
                    this_version_texts[media_type] = raw_text
                    this_version_ids[media_type] = created.id
                elif existing and this_version_ids.get(existing.media_type) == replaced_id:
                    # OPEN-539: the replaced row's text no longer exists in the table, so it is
                    # no baseline either (as `recompute-diff-order` would see the bill).
                    del this_version_texts[existing.media_type]
                    del this_version_ids[existing.media_type]
            except IntegrityError:
                # OPEN-107: ask the database which of two very different things just
                # happened, instead of reporting both as a "natural-key conflict" and
                # failing the run for either.
                #
                # `run-archive.sh` states in its own header that running an archive
                # concurrently with a scrape for the SAME jurisdiction is safe, because the
                # skip check above makes an already-archived version a cheap DB check. That
                # was not true in practice: two archivers can both pass the skip check for
                # the same link before either inserts, and the loser's IntegrityError was
                # counted as a conflict -- which `archive()` turns into sys.exit(1), failing
                # the whole run. Confirmed as the cause of this ticket: the WA and VA full
                # archives of 2026-07-28 ran simultaneously (both started 12:28:52) and
                # produced 347 and 1,820 of these respectively, and every affected document
                # is archived today, which a genuinely broken dedup key could not produce.
                #
                # A row now existing for this exact natural key means the other writer got
                # there first and the document IS safely archived -- nothing was lost, and
                # there is nothing to alarm about. Anything else is a real uniqueness
                # violation and keeps its loud, run-failing treatment.
                #
                # The winner is authoritative, deliberately (raised on /pm-review): the two
                # runs fetched independently and their bytes are not guaranteed identical,
                # but the four-field natural key IS the archival identity for this table
                # (see BillVersionDocument's own docstring), so the loser's bytes have
                # nowhere to go. That also makes the recovered row -- not this run's own
                # fetch -- what feeds the diff baseline below, which is the consistent
                # choice: the baseline must match the text actually stored.
                stored = BillVersionDocument.objects.filter(
                    bill=bill,
                    version_note=version.note,
                    version_date=version.date,
                    source_url=link.url,
                ).first()
                if stored is not None:
                    click.secho(
                        f"NOTE already archived by a concurrent run: {bill.identifier} "
                        f"{version.note} ({version.date}) {link.url}",
                        fg="yellow",
                    )
                    counters["concurrent_writes"] += 1
                    # Feed the baseline from the row the other run wrote. Without this the
                    # losing run drops this media type from the version's baseline entirely,
                    # so the NEXT version's document of the same rendering gets a diff
                    # against an older version, or none at all -- a silent lineage gap
                    # caused purely by having lost a race.
                    if _feeds_baseline(stored, jurisdiction_name):
                        this_version_texts[stored.media_type] = stored.raw_text
                        this_version_ids[stored.media_type] = stored.id
                else:
                    click.secho(
                        f"WARNING unexplained integrity error archiving "
                        f"{bill.identifier} {version.note} ({version.date}) {link.url} "
                        f"-- no row exists for this natural key",
                        fg="red",
                    )
                    counters["conflicts"] += 1

        if this_version_texts and not is_unknown_position:
            # OPEN-217: every media type this version produced becomes the baseline for its
            # own next appearance. The XML-over-PDF preference that used to pick a single
            # representative text here is gone -- with per-media baselines there is nothing
            # left to prefer.
            prior_by_media.update(this_version_texts)
            prior_id_by_media.update(this_version_ids)

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
    # OPEN-323: the connection-failure count is per RUN (it spans bills), so a run starts at zero
    # rather than inheriting whatever an earlier archive() call in this process left behind.
    _reset_connect_failure_count()
    global _not_found_recheck_unanswered
    _not_found_recheck_unanswered = False  # OPEN-506: per run too
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
        "concurrent_writes": 0,
        "s3_verified": 0,
        "s3_unverified": 0,
        "persist_errors": 0,
    }
    bill_count = 0
    unsaved_documents: list[tuple[str, str]] = []
    # OPEN-237: this loop otherwise prints nothing at all for a bill that archives cleanly --
    # every click.secho above is on an error/warning path only, and the one line that always
    # prints (the run's own summary) only appears once, at the very end. That leaves no way to
    # tell "still running, making progress" apart from "still running, stuck" from the log
    # alone, which is exactly what a stalled-run detector needs. A plain, timestamped heartbeat
    # at most once per interval -- not once per bill, which would flood the log on a large
    # jurisdiction -- is the minimal fix: cheap, always fires regardless of error/success mix,
    # and needs no state kept between runs.
    #
    # The bracketed "[YYYY-MM-DD HH:MM:SS]" prefix matches run-archive.sh's own log() format
    # exactly (local time, same strftime pattern) even though it's written here in Python, not
    # bash -- os-status already greps that exact bracket shape for other lines (last_log_ts()),
    # so this heartbeat is parseable with the same existing pattern, not a new one.
    last_heartbeat = time.monotonic()
    for bill in bills:
        bill_count += 1
        try:
            bill_counters = archive_bill_versions(bill, unsaved_documents)
        except ScrapeError as e:
            # OPEN-52: a sustained WAF block (consecutive-block circuit breaker tripped) must
            # be visible as a real failure, not a silent exit-0 run with a high "blocked" count
            # nobody's alerting on.
            click.secho(f"{state}: aborted -- {e}", fg="red")
            sys.exit(1)
        for key, value in bill_counters.items():
            totals[key] += value
        # Checked after the bill is actually done, not before -- otherwise "N bills processed
        # so far" would count a bill that's still mid-fetch as processed the moment its
        # iteration starts, overstating progress by one entry for as long as that bill takes.
        now = time.monotonic()
        if now - last_heartbeat >= _ARCHIVE_HEARTBEAT_INTERVAL_S:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            click.echo(f"[{ts}] {state}: heartbeat, {bill_count} bills processed so far")
            last_heartbeat = now

    status_color = "green"
    if totals["conflicts"]:
        status_color = "red"
    elif (
        totals["fetch_errors"]
        or totals["blocked"]
        or totals["extract_errors"]
        or totals["s3_unverified"]
        # OPEN-107: worth seeing, not worth failing over. Every affected document is
        # archived; the only cost is this run having done redundant fetching.
        or totals["concurrent_writes"]
        # OPEN-263: a local-persist failure used to increment nothing at all, so a systemic
        # write failure (e.g. a container permission bug -- confirmed live 2026-09-09) looked
        # identical to "nothing new to archive." Same severity class as fetch_errors: a real
        # per-document failure, not a run-invalidating uniqueness violation.
        or totals["persist_errors"]
    ):
        status_color = "yellow"

    click.secho(
        f"{state}: {bill_count} bills checked | "
        f"fetched={totals['fetched']} skipped={totals['skipped']} "
        f"archived={totals['archived']} fetch_errors={totals['fetch_errors']} "
        f"blocked={totals['blocked']} "
        f"extract_errors={totals['extract_errors']} conflicts={totals['conflicts']} "
        f"concurrent_writes={totals['concurrent_writes']} "
        f"s3_verified={totals['s3_verified']} s3_unverified={totals['s3_unverified']} "
        f"persist_errors={totals['persist_errors']}",
        fg=status_color,
    )
    # OPEN-487: name every document that was fetched but could not be saved (address and
    # reason), not only the persist_errors count. They are retried by the next run.
    for url, reason in unsaved_documents:
        click.secho(f"{state}: not saved: {url} ({reason})", fg="yellow")
    if totals["conflicts"]:
        # A conflict means our own uniqueness assumption was wrong somewhere — worth a
        # non-zero exit so this surfaces as a failure in run-scrape.sh, not just a log line.
        #
        # OPEN-107: `concurrent_writes` deliberately does NOT reach here. Losing a race to
        # another archiver leaves the document archived and the run's work correct, so
        # failing on it made `run-archive.sh`'s documented "safe to run concurrently"
        # false -- and, before the 2026-07-31 archiver/scraper split, left the incremental
        # cutoff stuck because the run never reported success.
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
    known stage (_STAGE_UNKNOWN) or is a known short procedural document for this jurisdiction
    (OPEN-224, `is_procedural_document()`), mirroring archive_bill_versions()'s own skip-diffing
    behavior for those. Callers decide whether to persist "changed" (see `recompute_diff_order`
    CLI command's --dry-run/--commit).
    """
    from openstates.data.models import BillVersionDocument

    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))
    return recomputed_diffs_for_documents(
        docs,
        jurisdiction_name=bill.legislative_session.jurisdiction.name,
        is_bill=bill.classification == ["bill"],
        bill_identifier=bill.identifier,
        current_versions=_current_version_keys(bill),
    )


def _current_version_keys(bill: typing.Any) -> typing.Optional[set]:
    """OPEN-569: the (note, date) of each of the bill's versions now, the natural key its stored documents carry, for
    a state whose versions the scraper dates (Alabama); None elsewhere. Alabama's re-scrape re-dates every version and
    turns its never-adopted substitutes into documents, so its stored copies under the old (note, date) must leave the
    lineage. Elsewhere nothing changes: on the Mac replica (2026-11-16) only Arizona (30) and Michigan (26) have stored
    documents that match no current version, and a recompute of those states keeps treating them as before."""
    if bill.legislative_session.jurisdiction.name not in _DATE_ORDERED_JURISDICTIONS:
        return None
    return set(bill.versions.values_list("note", "date"))


def recomputed_diffs_for_documents(
    docs: list,
    *,
    jurisdiction_name: str,
    is_bill: bool,
    bill_identifier: str,
    current_versions: typing.Optional[set] = None,
) -> dict[str, list]:
    """The ordering-and-diffing half of `recompute_bill_diff_order`, split out (OPEN-211) so it
    can be exercised without a database -- it reads nothing but plain attributes off each
    document, while its caller above is the part that queries.

    OPEN-219: `jurisdiction_name` and `is_bill` are REQUIRED keyword arguments, with no
    defaults, and that is the point. This function used to apply none of the four
    per-jurisdiction pre-diff cleaners `archive_bill_versions` applies, so the two paths
    produced different diffs from identical inputs. A default would have preserved exactly
    that failure -- a caller that forgot would silently get uncleaned output, which is how
    the divergence survived unnoticed from 2026-08-14 to 2026-08-29. Making the caller state
    the jurisdiction means it cannot be skipped by omission.

    OPEN-382: `bill_identifier` is required for the same reason -- Arizona's Engrossed Versions are
    placed by the bill's origin chamber, so a caller that left it out would order Arizona
    differently from `archive_bill_versions`.

    They are parameters rather than values read off a model because this function's whole
    contract is that it touches only plain attributes: that is what lets it be tested with no
    database, and what lets `refresh-extraction` simulate a dry run with stand-in objects.

    OPEN-569: `current_versions`, the bill's current (note, date) pairs (`_current_version_keys`), when given, keeps
    every stored document whose pair is no longer a version of the bill out of the lineage (no diff, not a baseline),
    as `archive_bill_versions` does by walking only the current versions. Such a document is a copy saved under an
    old note or date (a version re-dated or renamed by the scraper) or a paper that is no longer a version (Alabama's
    substitutes that were offered and never adopted). None treats every document as current.

    OPEN-539: the result also has "baselines", a list of (doc, baseline_doc_or_None) for every document, in walk
    order: the document the recomputed comparison is taken against (the one `_diff_baseline` chose), None exactly
    when the recomputed comparison is None. Callers store `baseline_doc.id` as `diff_baseline_id` with the
    comparison."""
    groups: dict[tuple, list] = {}
    for doc in docs:
        groups.setdefault((doc.version_note, doc.version_date), []).append(doc)

    origin = _origin_chamber(bill_identifier)
    ordered_keys = sorted(
        groups.keys(),
        key=lambda k: _version_sort_key(
            k[0], k[1], [d.source_url for d in groups[k]], origin, jurisdiction_name
        ),
    )

    unchanged = []
    changed = []
    # OPEN-211: one baseline PER MEDIA TYPE, not one shared baseline for the whole version.
    #
    # This used to keep a single `prior_text` (PDF-preferred) and diff every document of the
    # next version against it, so an XML or HTML document was compared against the previous
    # version's PDF. Two different renderings of the same words never align, so the result was
    # a full rewrite rather than a changelog -- measured on Utah SB 0059, new XML against prior
    # PDF: one hunk, 20,161 chars, "@@ -1,133 +1,148 @@".
    #
    # That is why re-extracting alone (OPEN-210/OPEN-212 gave XML and WA HTML real line
    # structure) does not by itself produce usable diffs: the comparison has to be
    # like-for-like. Each document now diffs against the previous version's document of its
    # own media type.
    #
    # A media type absent from one version does not reset its lineage -- only the types present
    # are updated -- so a version that happens to ship PDF-only does not orphan the XML chain.
    prior_by_media: dict[str, str] = {}
    prior_doc_by_media: dict[str, typing.Any] = {}  # OPEN-539: the document behind each text
    baselines = []
    for note, date in ordered_keys:
        # OPEN-224: parity with archive_bill_versions() above -- same helper, same inputs.
        is_unknown_position = (
            _note_stage(note)[0] == _STAGE_UNKNOWN
            or _is_procedural_document(jurisdiction_name, note)
            or (current_versions is not None and (note, date) not in current_versions)
        )
        group_texts: dict[str, str] = {}
        group_docs: dict[str, typing.Any] = {}
        # OPEN-578: the archiver's walk order, so the last of each media type (the next version's baseline) is the
        # last by source_url, not the last by row id.
        for doc in _version_document_order(
            groups[(note, date)], lambda d: d.media_type, lambda d: d.source_url
        ):
            new_diff = None
            baseline_doc = None
            prior_text, prior_media_type = _diff_baseline(
                prior_by_media, doc.media_type, jurisdiction_name
            )
            if (
                prior_text is not None
                and _feeds_baseline(doc, jurisdiction_name)
                and not is_unknown_position
            ):
                # OPEN-219: the same cleaning archive_bill_versions applies. Both paths are
                # same-media since OPEN-217 except Massachusetts' chapter law (OPEN-223), and
                # both choose the baseline with the same _diff_baseline -- so the Michigan and
                # Virginia cleaners' cross-media reflow branch stays unreachable from here,
                # exactly as it is from the archive path.
                new_diff = _compare_texts(
                    prior_text,
                    doc.raw_text,
                    jurisdiction_name=jurisdiction_name,
                    is_bill=is_bill,
                    prior_media_type=prior_media_type,
                    cur_media_type=doc.media_type,
                )
                baseline_doc = prior_doc_by_media[prior_media_type]
            baselines.append((doc, baseline_doc))
            if new_diff != doc.diff_from_previous_version:
                changed.append((doc, new_diff))
            else:
                unchanged.append(doc)
            if _feeds_baseline(doc, jurisdiction_name):
                group_texts[doc.media_type] = doc.raw_text
                group_docs[doc.media_type] = doc
        if group_texts and not is_unknown_position:
            prior_by_media.update(group_texts)
            prior_doc_by_media.update(group_docs)

    return {"unchanged": unchanged, "changed": changed, "baselines": baselines}


def _baseline_changes(result: dict, *, diffs_written: bool) -> typing.Tuple[list, int]:
    """
    OPEN-539: from one bill's `recomputed_diffs_for_documents` result, [(doc, old_id, new_id, baseline_doc)] for each
    document whose stored `diff_baseline_id` is not the document its comparison is taken against (None where there
    is no comparison), and how many documents were left out.

    A document is left out only when `diffs_written` is False (`--baseline-only`) and its recomputed comparison
    differs from the stored one: the recomputed baseline did not produce the stored text, so stamping it would name a
    document the stored comparison was not made against. A full recompute writes that comparison and its baseline
    together.
    """
    would_change = {id(doc) for doc, _ in result["changed"]}
    changes = []
    left_out = 0
    for doc, baseline_doc in result["baselines"]:
        if not diffs_written and id(doc) in would_change:
            left_out += 1
            continue
        want = baseline_doc.id if baseline_doc is not None else None
        if doc.diff_baseline_id != want:
            changes.append((doc, doc.diff_baseline_id, want, baseline_doc))
    return changes, left_out


def _baseline_stamp_plan(bills: typing.Any) -> tuple:
    """OPEN-539: the `--baseline-only` plan for `bills`: (bills walked, CSV rows to stamp, counts). Reads only."""
    rows = []
    counts = {"comparisons": 0, "comparison_would_change": 0}
    bill_count = 0
    for bill in bills:
        bill_count += 1
        result = recompute_bill_diff_order(bill)
        counts["comparisons"] += sum(
            1 for doc, _ in result["baselines"] if doc.diff_from_previous_version is not None
        )
        changes, left_out = _baseline_changes(result, diffs_written=False)
        counts["comparison_would_change"] += left_out
        for doc, old_id, new_id, baseline_doc in changes:
            rows.append(
                {
                    "doc": doc,
                    "csv": [
                        doc.id,
                        bill.id,
                        bill.identifier,
                        bill.legislative_session.identifier,
                        doc.version_note,
                        doc.version_date,
                        doc.media_type,
                        "" if old_id is None else old_id,
                        "" if new_id is None else new_id,
                        baseline_doc.version_note if baseline_doc is not None else "",
                        baseline_doc.version_date if baseline_doc is not None else "",
                        baseline_doc.media_type if baseline_doc is not None else "",
                    ],
                    "old": old_id,
                    "new": new_id,
                }
            )
    return bill_count, rows, counts


_BASELINE_LIST_HEADER = [
    "doc_id",
    "bill_id",
    "identifier",
    "session",
    "version_note",
    "version_date",
    "media_type",
    "old_diff_baseline_id",
    "new_diff_baseline_id",
    "baseline_note",
    "baseline_date",
    "baseline_media_type",
]


def _write_baseline_list(path: str, rows: list) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_BASELINE_LIST_HEADER)
        for row in rows:
            writer.writerow(row["csv"])


_COMPARISON_LIST_HEADER = [
    "doc_id",
    "bill_id",
    "identifier",
    "session",
    "version_note",
    "version_date",
    "media_type",
    "comparison",
    "old_diff_baseline_id",
    "old_comparison_made_against",
    "new_diff_baseline_id",
    "baseline_note",
    "baseline_date",
    "baseline_media_type",
]


def _stored_comparison_source(doc: typing.Any, docs: list, jurisdiction_name: str, is_bill: bool) -> str:
    """OPEN-578: the ids of the documents of the same bill whose text reproduces `doc`'s stored comparison (the
    document it was made against; several when their texts clean to the same text), joined by ";", or "" when there is
    no stored comparison or no document reproduces it. Only documents of `doc`'s own media type and Massachusetts'
    cross-media baseline type are tried (`_diff_baseline`)."""
    stored = doc.diff_from_previous_version
    if stored is None:
        return ""
    found = []
    types = {doc.media_type}
    if jurisdiction_name == "Massachusetts" and doc.media_type in _MA_CROSS_MEDIA_BASELINE:
        types.add(_MA_CROSS_MEDIA_BASELINE[doc.media_type])
    for other in docs:
        if other is doc or other.media_type not in types or not _feeds_baseline(other, jurisdiction_name):
            continue
        again = _compare_texts(
            other.raw_text,
            doc.raw_text,
            jurisdiction_name=jurisdiction_name,
            is_bill=is_bill,
            prior_media_type=other.media_type,
            cur_media_type=doc.media_type,
        )
        if again == stored:
            found.append(str(other.id))
    return ";".join(found)


def _comparison_change_rows(bill: typing.Any, result: dict) -> list:
    """OPEN-578: `recompute-diff-order --list` without `--baseline-only`: one CSV row per document of `bill` whose
    stored comparison or `diff_baseline_id` the recompute would change. `comparison` is corrected, nulled, new or
    unchanged; the old baseline is the stored `diff_baseline_id` and, since comparisons stored before OPEN-539 name
    none, also every document whose text reproduces the stored comparison (`old_comparison_made_against`; for an
    unchanged comparison whose baseline moves, both the old and the new baseline)."""
    jurisdiction_name = bill.legislative_session.jurisdiction.name
    is_bill = bill.classification == ["bill"]
    new_diff = {id(doc): diff for doc, diff in result["changed"]}
    docs = [doc for doc, _ in result["baselines"]]
    rows = []
    for doc, baseline_doc in result["baselines"]:
        new_id = baseline_doc.id if baseline_doc is not None else None
        if id(doc) not in new_diff and doc.diff_baseline_id == new_id:
            continue
        if id(doc) not in new_diff:
            comparison = "unchanged"
        elif doc.diff_from_previous_version is None:
            comparison = "new"
        else:
            comparison = "nulled" if new_diff[id(doc)] is None else "corrected"
        made_against = _stored_comparison_source(doc, docs, jurisdiction_name, is_bill)
        rows.append(
            [
                doc.id,
                bill.id,
                bill.identifier,
                bill.legislative_session.identifier,
                doc.version_note,
                doc.version_date,
                doc.media_type,
                comparison,
                "" if doc.diff_baseline_id is None else doc.diff_baseline_id,
                made_against,
                "" if new_id is None else new_id,
                baseline_doc.version_note if baseline_doc is not None else "",
                baseline_doc.version_date if baseline_doc is not None else "",
                baseline_doc.media_type if baseline_doc is not None else "",
            ]
        )
    return rows


def _baseline_counts_text(changes: typing.Iterable[tuple]) -> str:
    """`set=N changed=N cleared=N` over (old_id, new_id) pairs."""
    tally = {"set": 0, "changed": 0, "cleared": 0}
    for old_id, new_id in changes:
        if old_id is None:
            tally["set"] += 1
        elif new_id is None:
            tally["cleared"] += 1
        else:
            tally["changed"] += 1
    return " ".join(f"{k}={v}" for k, v in tally.items())


def _stamp_baselines_only(
    state: str, bills: typing.Any, commit: bool, expect: typing.Optional[int], list_path: typing.Optional[str]
) -> None:
    """
    OPEN-539: `recompute-diff-order --baseline-only`, the stamping pass for comparisons stored before
    `diff_baseline_id` existed. Same shape as `remove-stale-documents`: a dry run by default; `--commit` needs
    `--list` (one CSV row per stamped document, named only after the commit), `--expect N` aborts unless exactly N
    documents would be stamped. Writes only `diff_baseline_id` (never the comparison, never `updated_at`, so no
    reader sees the documents as changed), and only where the recomputed comparison equals the stored one. The plan's
    stamping pass also confirms that no stored comparison changes, so `--commit` refuses a state where any stored
    comparison is not reproduced (`not_stamped_comparison_would_change` above 0): there the answer is the full
    recompute, which writes each comparison with its baseline. After the commit it prints the state's completeness
    count (stored comparisons still without a baseline; 0 means the state is stamped).

    The commit locks the documents and plans again inside the transaction, so the stamps written are the ones the
    list records. An archive inserting a new document meanwhile cannot make a stamp wrong: it never rewrites a stored
    comparison, and a stamp is written only where the stored comparison is reproduced from its baseline.
    """
    from openstates.data.models import BillVersionDocument

    written_path = f"{list_path}.partial" if commit and list_path else list_path
    if commit:
        try:
            with transaction.atomic():
                list(
                    BillVersionDocument.objects.select_for_update()
                    .filter(bill__in=bills)
                    .values_list("id", flat=True)
                )
                bill_count, rows, counts = _baseline_stamp_plan(bills)
                if counts["comparison_would_change"]:
                    raise click.ClickException(
                        f"{counts['comparison_would_change']} stored comparisons are not what the recompute gives, so "
                        "they cannot be stamped; run the full recompute for this state instead (dry run first). "
                        "Nothing written"
                    )
                if expect is not None and expect != len(rows):
                    raise click.ClickException(
                        f"{len(rows)} documents would be stamped, not the expected {expect}; nothing written"
                    )
                _write_baseline_list(written_path, rows)
                docs = []
                for row in rows:
                    row["doc"].diff_baseline_id = row["new"]
                    docs.append(row["doc"])
                BillVersionDocument.objects.bulk_update(docs, ["diff_baseline_id"], batch_size=1000)
                without_baseline = BillVersionDocument.objects.filter(
                    bill__in=bills, diff_from_previous_version__isnull=False, diff_baseline_id__isnull=True
                ).count()
        except BaseException:
            if os.path.exists(written_path):
                os.remove(written_path)
            raise
        try:
            os.replace(written_path, list_path)
        except OSError as e:
            raise click.ClickException(
                f"{state}: COMMITTED, but the change list could not take its name ({e}); "
                f"it is complete at {written_path}. Do not run again: the commit is done"
            )
    else:
        bill_count, rows, counts = _baseline_stamp_plan(bills)
        if list_path:
            _write_baseline_list(list_path, rows)

    mode = "COMMITTED" if commit else "DRY RUN"
    color = "green" if commit else "yellow"
    click.secho(
        f"{state}: [{mode}] {bill_count} bills checked | diffs untouched (--baseline-only)",
        fg=color,
    )
    click.secho(
        f"{state}: [{mode}] diff_baseline_id: comparisons={counts['comparisons']} stamp={len(rows)} "
        f"({_baseline_counts_text((r['old'], r['new']) for r in rows)}) "
        f"not_stamped_comparison_would_change={counts['comparison_would_change']}",
        fg=color,
    )
    if commit:
        click.secho(f"  after: comparisons_without_baseline={without_baseline}", fg=color)
    if list_path:
        click.secho(f"  change list: {list_path}", fg=color)


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
@click.option(
    "--flags-only",
    is_flag=True,
    default=False,
    help="OPEN-498: set is_bill_version only and leave every stored diff exactly as it is "
    "(Massachusetts: its stored diffs are the only record of which bill became which law)",
)
@click.option(
    "--baseline-only",
    is_flag=True,
    default=False,
    help="OPEN-539: set diff_baseline_id only, on documents whose stored comparison the recompute "
    "reproduces; every stored diff and is_bill_version stays exactly as it is",
)
@click.option(
    "--expect",
    type=int,
    default=None,
    help="with --baseline-only: abort before writing unless exactly this many documents would be "
    "stamped (the agreed dry-run count)",
)
@click.option(
    "--list",
    "list_path",
    default=None,
    help="write one CSV row per document whose diff_baseline_id would change (with --baseline-only, "
    "required with --commit); without --baseline-only (OPEN-578), one row per document whose stored "
    "comparison or diff_baseline_id would change, with the old and new baseline",
)
def recompute_diff_order(
    state: str,
    session: str = None,
    commit: bool = False,
    flags_only: bool = False,
    baseline_only: bool = False,
    expect: int = None,
    list_path: str = None,
) -> None:
    if baseline_only and flags_only:
        raise click.ClickException("--baseline-only and --flags-only each leave the other's column alone; pick one")
    if expect is not None and not baseline_only:
        raise click.ClickException("--expect goes with --baseline-only")
    if list_path and flags_only:
        raise click.ClickException("--list lists comparisons; --flags-only leaves them alone")
    if baseline_only and commit and not list_path:
        raise click.ClickException(
            "--commit needs --list FILE: that file is the record of what was stamped"
        )
    if commit and list_path and (
        os.path.exists(list_path) or os.path.exists(f"{list_path}.partial")
    ):
        raise click.ClickException(
            f"{list_path} (or its .partial) already exists: an earlier record is never overwritten; nothing written"
        )

    init_django()
    from openstates.data.models import Bill, BillVersionDocument

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

    if baseline_only:
        _stamp_baselines_only(state, bills, commit, expect, list_path)
        return

    total_unchanged = 0
    total_corrected = 0
    total_nulled = 0
    # OPEN-492: is_bill_version, counted separately so the summary line above keeps its format.
    flag_set = 0  # was empty (not decided yet), now has a value
    flag_changed = 0  # had the other value
    flag_not_a_version = 0  # documents the rules say are NOT a bill version, whatever was stored
    # OPEN-539: diff_baseline_id, written with each comparison: (old, new) per document that changes
    baseline_changes: list = []
    # OPEN-578: --list, one row per document whose comparison or baseline changes. Opened before anything is written,
    # and each bill's rows are written before that bill's saves; a commit writes to FILE.partial and names it FILE
    # only when it finishes, so an interrupted commit leaves the record of what it wrote as FILE.partial.
    change_list = None
    if list_path:
        change_list = open(f"{list_path}.partial" if commit else list_path, "w", newline="")
        change_writer = csv.writer(change_list)
        change_writer.writerow(_COMPARISON_LIST_HEADER)
    change_count = 0
    bill_count = 0

    for bill in bills:
        bill_count += 1
        if flags_only:
            # Every document goes through the flag loop below; no diff is computed or written.
            result = {
                "unchanged": list(
                    BillVersionDocument.objects.filter(bill=bill).order_by("id")
                ),
                "changed": [],
            }
        else:
            result = recompute_bill_diff_order(bill)
            total_unchanged += len(result["unchanged"])
            if change_list is not None:  # before a commit below sets the new values on the same objects
                rows = _comparison_change_rows(bill, result)
                change_writer.writerows(rows)
                change_list.flush()
                change_count += len(rows)
        jurisdiction_name = bill.legislative_session.jurisdiction.name
        fields_by_doc: dict[int, tuple] = {}
        for doc, new_diff in result["changed"]:
            if new_diff is None:
                total_nulled += 1
            else:
                total_corrected += 1
            if commit:
                doc.diff_from_previous_version = new_diff
                fields_by_doc[doc.id] = (doc, {"diff_from_previous_version"})
        if not flags_only:
            # OPEN-539: every comparison this run leaves stored names the document it was taken against.
            for doc, old_id, new_id, _ in _baseline_changes(result, diffs_written=True)[0]:
                baseline_changes.append((old_id, new_id))
                if commit:
                    doc.diff_baseline_id = new_id
                    fields_by_doc.setdefault(doc.id, (doc, set()))[1].add("diff_baseline_id")
        for doc in result["unchanged"] + [d for d, _ in result["changed"]]:
            want = _is_bill_version(
                jurisdiction_name, doc.version_note, bill.title, doc.raw_text
            )
            if not want:
                flag_not_a_version += 1
            if doc.is_bill_version is None:
                flag_set += 1
            elif doc.is_bill_version != want:
                flag_changed += 1
            else:
                continue
            if commit:
                doc.is_bill_version = want
                fields_by_doc.setdefault(doc.id, (doc, set()))[1].add("is_bill_version")
        for doc, fields in fields_by_doc.values():
            doc.save(update_fields=sorted(fields))

    mode = "COMMITTED" if commit else "DRY RUN"
    if flags_only:
        diff_summary = "diffs untouched (--flags-only)"
    else:
        diff_summary = f"unchanged={total_unchanged} corrected={total_corrected} nulled={total_nulled}"
    click.secho(
        f"{state}: [{mode}] {bill_count} bills checked | {diff_summary}",
        fg="green" if commit else "yellow",
    )
    click.secho(
        f"{state}: [{mode}] is_bill_version: set={flag_set} changed={flag_changed} "
        f"not_a_version={flag_not_a_version}",
        fg="green" if commit else "yellow",
    )
    if not flags_only:
        click.secho(
            f"{state}: [{mode}] diff_baseline_id: {_baseline_counts_text(baseline_changes)}",
            fg="green" if commit else "yellow",
        )
    if change_list is not None:
        change_list.close()
        if commit:
            os.replace(f"{list_path}.partial", list_path)
        click.secho(f"  change list: {list_path} ({change_count} documents)", fg="green" if commit else "yellow")


def _label_change_counts(pairs: typing.Iterable[tuple]) -> typing.List[str]:
    """One line per (old labels -> new labels) change, most frequent first: the summary `relabel-non-bills` and
    `relabel` print."""
    by_label: dict[tuple, int] = {}
    for old, new in pairs:
        key = (", ".join(old) or "(none)", ", ".join(new) or "(none)")
        by_label[key] = by_label.get(key, 0) + 1
    return [
        f"  {old} -> {new}: {count}"
        for (old, new), count in sorted(by_label.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _write_if_unchanged(model: typing.Any, pk: typing.Any, where: dict, fields: dict, name: str) -> None:
    """Write `fields` to one row only if it still matches `where` (what was read); otherwise abort the run, so an
    import landing mid-run rolls the whole transaction back instead of being overwritten. Shared by `relabel-non-bills`
    and `relabel`."""
    if model.objects.filter(pk=pk, **where).update(**fields) != 1:
        raise click.ClickException(
            f"{name} changed since it was read; rolled back, nothing written"
        )


@main.command(
    help="OPEN-517: relabel bill rows that are not bills (Massachusetts reports, communications, "
    "orders, study orders) with what they are; keeps the old label in extras so --undo restores it"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="write the new labels; default is a dry run that only reports counts",
)
@click.option(
    "--undo",
    is_flag=True,
    default=False,
    help="put back the label saved by an earlier --commit (with --commit; a dry run only counts)",
)
@click.option(
    "--expect",
    type=int,
    default=None,
    help="abort before writing unless exactly this many bills would change (the agreed dry-run count)",
)
@click.option(
    "--list",
    "list_path",
    default=None,
    help="write one CSV row per bill that would change (id, identifier, old, new, title)",
)
def relabel_non_bills(
    state: str,
    session: str = None,
    commit: bool = False,
    undo: bool = False,
    expect: int = None,
    list_path: str = None,
) -> None:
    """
    Only bills whose title the OPEN-498 rule calls not-a-bill are touched, through `non_bill_kind()`.
    Left alone, and counted: amendment papers (the scraper still yields them, so a new label would be
    overwritten on the next run; OPEN-509 moves them) and any bill with a recorded vote (Ramon,
    2026-11-08: those are looked at one by one). Nothing is deleted. The write is a queryset update,
    so `updated_at` does not move: nothing downstream (broker sync, LegBot, search) reads the label,
    and a bump would make them re-process thousands of items for no change of theirs.
    """
    init_django()
    from openstates.data.models import Bill

    bills = Bill.objects.filter(legislative_session__jurisdiction_id=abbr_to_jid(state))
    if session:
        bills = bills.filter(legislative_session__identifier=session)
    bills = (
        bills.select_related("legislative_session__jurisdiction")
        .annotate(vote_count=Count("votes", distinct=True))
        .order_by("id")
    )

    changes = []  # (bill, old classification, new classification, new extras)
    skipped: dict[str, int] = {}
    unchanged = 0
    for bill in bills:
        extras = dict(bill.extras or {})
        if undo:
            if ORIGINAL_CLASSIFICATION_KEY not in extras:
                continue
            original = extras.pop(ORIGINAL_CLASSIFICATION_KEY)
            changes.append((bill, list(bill.classification), list(original), extras))
            continue
        kind = _non_bill_kind(
            bill.legislative_session.jurisdiction.name,
            bill.title,
            bill.actions.values_list("description", flat=True),
        )
        if kind is None:
            continue
        if kind == _NON_BILL_AMENDMENT:
            skipped["amendment paper"] = skipped.get("amendment paper", 0) + 1
            continue
        if bill.vote_count:
            skipped["has recorded votes"] = skipped.get("has recorded votes", 0) + 1
            continue
        if list(bill.classification) == [kind]:
            unchanged += 1
            continue
        # A second run keeps the first original: the label the scraper gave, not ours.
        extras.setdefault(ORIGINAL_CLASSIFICATION_KEY, list(bill.classification))
        changes.append((bill, list(bill.classification), [kind], extras))

    if list_path:
        with open(list_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["bill_id", "identifier", "session", "old", "new", "title"])
            for bill, old, new, _ in changes:
                writer.writerow(
                    [
                        bill.id,
                        bill.identifier,
                        bill.legislative_session.identifier,
                        ";".join(old),
                        ";".join(new),
                        " ".join((bill.title or "").split()),
                    ]
                )

    if commit and expect is not None and expect != len(changes):
        raise click.ClickException(
            f"{len(changes)} bills would change, not the expected {expect}; nothing written"
        )

    if commit:
        with transaction.atomic():
            for bill, old, new, extras in changes:
                # Write only if the row is still what was read: same label, same extras, and (for a
                # relabel) still no recorded vote. An import or a vote landing mid-run aborts the
                # whole run instead of being overwritten.
                where = {"classification": old, "extras": bill.extras}
                if not undo:
                    where["votes__isnull"] = True
                _write_if_unchanged(
                    Bill,
                    bill.pk,
                    where,
                    {"classification": new, "extras": extras},
                    f"{bill.id} ({bill.identifier})",
                )

    mode = "COMMITTED" if commit else "DRY RUN"
    action = "restored" if undo else "relabelled"
    color = "green" if commit else "yellow"
    click.secho(
        f"{state}: [{mode}] {action}={len(changes)} already_labelled={unchanged} "
        + " ".join(f"skipped_{k.replace(' ', '_')}={v}" for k, v in sorted(skipped.items())),
        fg=color,
    )
    for line in _label_change_counts((old, new) for _, old, new, _ in changes):
        click.secho(line, fg=color)


RELABEL_LIST_COLUMNS = [
    "kind",
    "id",
    "bill_id",
    "identifier",
    "session",
    "order",
    "date",
    "chamber",
    "text",
    "old",
    "new",
]


def _new_labels(rule: typing.Callable, name: str, what: str, old: list, **context: typing.Any) -> typing.Optional[list]:
    """
    OPEN-546: the labels `rule` gives an item, or None when they are the ones stored (compared as a set: a rule that
    only reorders the same labels changes nothing). The rule is asked again on its own answer; an answer that would move
    again on a second run is refused, so `relabel` run twice changes nothing the second time.
    """
    new = list(dict.fromkeys(rule(classification=list(old), **context)))
    if set(new) == set(old):
        return None
    again = list(rule(classification=list(new), **context))
    if set(again) != set(new):
        raise click.ClickException(
            f"{name} gives {what} {old} -> {new}, then {again} when asked again: it must give the same answer on its "
            "own output; nothing written"
        )
    return new


def _relabel_plan(bills: typing.Any, classify_bill: typing.Any, classify_action: typing.Any) -> tuple:
    """OPEN-546: every stored bill and action of `bills` whose labels the state's current rules change, as change-list
    rows (`RELABEL_LIST_COLUMNS`, labels as lists), bills first, then actions in bill and stored order; and the
    number of items the rules leave as stored, by kind."""
    from openstates.data.models import BillAction

    changes = []
    unchanged = {"bill": 0, "action": 0}
    if classify_bill:
        for row in bills.order_by("id").values(
            "id", "identifier", "title", "classification", "legislative_session__identifier"
        ):
            new = _new_labels(
                classify_bill,
                "classify_bill",
                f"bill {row['identifier']}",
                list(row["classification"]),
                identifier=row["identifier"],
                title=row["title"],
            )
            if new is None:
                unchanged["bill"] += 1
                continue
            changes.append(
                dict(
                    kind="bill",
                    id=row["id"],
                    bill_id=row["id"],
                    identifier=row["identifier"],
                    session=row["legislative_session__identifier"],
                    order="",
                    date="",
                    chamber="",
                    text=" ".join((row["title"] or "").split()),
                    old=list(row["classification"]),
                    new=new,
                    read={"identifier": row["identifier"], "title": row["title"]},
                )
            )
    if classify_action:
        actions = (
            BillAction.objects.filter(bill__in=bills)
            .order_by("bill_id", "order", "id")
            .values(
                "id",
                "bill_id",
                "bill__identifier",
                "bill__legislative_session__identifier",
                "order",
                "date",
                "description",
                "classification",
                "organization__classification",
            )
        )
        for row in actions.iterator():
            new = _new_labels(
                classify_action,
                "classify_action",
                f"action {row['id']} of {row['bill__identifier']} ({row['description']!r})",
                list(row["classification"]),
                description=row["description"],
                chamber=row["organization__classification"],
                date=row["date"],
            )
            if new is None:
                unchanged["action"] += 1
                continue
            changes.append(
                dict(
                    kind="action",
                    id=row["id"],
                    bill_id=row["bill_id"],
                    identifier=row["bill__identifier"],
                    session=row["bill__legislative_session__identifier"],
                    order=row["order"],
                    date=row["date"],
                    chamber=row["organization__classification"],
                    text=row["description"],
                    old=list(row["classification"]),
                    new=new,
                    read={
                        "bill_id": row["bill_id"],
                        "description": row["description"],
                        "date": row["date"],
                        "organization__classification": row["organization__classification"],
                    },
                )
            )
    return changes, unchanged


def _undo_plan(bills: typing.Any, undo_path: str) -> list:
    """OPEN-546: the changes of a committed `relabel --list` file, turned round (each item back to its old labels).
    Refuses a file that names an item outside `bills`, so a list cannot be applied to the wrong state."""
    try:
        with open(undo_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            columns = reader.fieldnames or []
    except OSError as e:
        raise click.ClickException(f"cannot read {undo_path}: {e}")
    if set(RELABEL_LIST_COLUMNS) - set(columns):
        raise click.ClickException(f"{undo_path} is not a `relabel` change list; nothing written")
    bill_ids = set(bills.values_list("id", flat=True))
    changes = []
    for row in rows:
        if row["kind"] not in ("bill", "action"):
            raise click.ClickException(f"{undo_path} has a row of kind {row['kind']!r}; nothing written")
        if row["bill_id"] not in bill_ids or (row["kind"] == "bill" and row["id"] != row["bill_id"]):
            raise click.ClickException(
                f"{undo_path} names {row['kind']} {row['id']} of bill {row['bill_id']}, not a bill of this state "
                "and session; nothing written"
            )
        changes.append(
            dict(
                row,
                old=[x for x in row["new"].split(";") if x],
                new=[x for x in row["old"].split(";") if x],
                # an action is written only if it is still on the bill the list names
                read={"bill_id": row["bill_id"]} if row["kind"] == "action" else {},
            )
        )
    return changes


# The bill fields `update_bill_fields` computes from a bill's actions (`importers/computed_fields.py`).
COMPUTED_BILL_FIELDS = (
    "first_action_date",
    "latest_action_date",
    "latest_action_description",
    "latest_passage_date",
)


class _BillPreview:
    """A stand-in bill for `update_bill_fields` in a dry run: its stored computed fields, and its actions in stored
    order with the new labels. `save` does nothing, so the real rule runs and nothing is written."""

    def __init__(self, actions: list, **fields: typing.Any) -> None:
        self.__dict__.update(fields)
        self.actions = self
        self._actions = actions

    def order_by(self, *_: str) -> list:
        return self._actions

    def save(self, *_: typing.Any, **__: typing.Any) -> None:
        pass


def _changed_fields(before: dict, after: typing.Any, counts: dict) -> bool:
    """Add one to `counts` for each computed field whose value differs; True when any did."""
    changed = [f for f in COMPUTED_BILL_FIELDS if getattr(after, f) != before[f]]
    for f in changed:
        counts[f] += 1
    return bool(changed)


def _computed_field_preview(changes: list) -> dict:
    """OPEN-615: per computed field, how many bills `update_bill_fields` would change once the action labels in
    `changes` are written, counted with the real rule on in-memory copies (a dry run writes nothing). A field that was
    already stale (an older import) is counted too: the commit rewrites it."""
    from ..importers.computed_fields import update_bill_fields
    from openstates.data.models import Bill, BillAction

    # Ordered as update_bill_fields reads them (by `order`); `id` only breaks ties, which the importer never leaves
    # (it numbers a bill's actions 0..n; 0 duplicate (bill, order) pairs on the production replica, 2026-11-17).
    new_labels = {c["id"]: c["new"] for c in changes if c["kind"] == "action"}
    bill_ids = {c["bill_id"] for c in changes if c["kind"] == "action"}
    actions: dict = {}
    for row in (
        BillAction.objects.filter(bill_id__in=bill_ids)
        .order_by("bill_id", "order", "id")
        .values("id", "bill_id", "date", "description", "classification")
        .iterator()
    ):
        actions.setdefault(row["bill_id"], []).append(
            SimpleNamespace(
                date=row["date"],
                description=row["description"],
                classification=new_labels.get(row["id"], row["classification"]),
            )
        )
    counts = dict.fromkeys(COMPUTED_BILL_FIELDS, 0)
    for stored in Bill.objects.filter(id__in=bill_ids).values("id", *COMPUTED_BILL_FIELDS):
        preview = _BillPreview(actions.get(stored["id"], []), **{f: stored[f] for f in COMPUTED_BILL_FIELDS})
        update_bill_fields(preview)
        _changed_fields(stored, preview, counts)
    return counts


@main.command(
    help="OPEN-546: re-apply a state's current label rules (classify_action and classify_bill in "
    "openstates/ddp_states/<state>.py) to its stored actions and bills (dry run by default; --commit writes, with "
    "--list naming the file that records every change)"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="write the new labels; default is a dry run that only reports",
)
@click.option(
    "--expect",
    type=int,
    default=None,
    help="abort before writing unless exactly this many items (bills and actions) would change (the agreed dry-run "
    "count)",
)
@click.option(
    "--list",
    "list_path",
    default=None,
    help="write one CSV row per bill or action that would change, with its old and new labels; required with --commit",
)
@click.option(
    "--undo",
    "undo_path",
    default=None,
    help="put back the old labels recorded in a committed --list file (the rollback inside the silent window)",
)
def relabel(
    state: str,
    session: str = None,
    commit: bool = False,
    expect: int = None,
    list_path: str = None,
    undo_path: str = None,
) -> None:
    """
    PLAN-state-specific-handling §6 Phase 3: when a state's action or bill rule moves into its `classify_action` or
    `classify_bill` (`openstates/ddp_states/<state>.py`), this pass applies it to everything already stored, in the
    state's silent window. Core's bill importer applies the same `classify_action` to every action it imports
    (`BillImporter.prepare_for_db`, OPEN-550), reading it as this pass does, so a re-scrape keeps the new labels
    (`classify_bill` is not applied on import yet). The generalization of `relabel-non-bills` (OPEN-517), with which it
    shares its write and summary code; that command keeps Massachusetts' own rule.

    Only labels change: an action's `classification` or a bill's `classification`, never its text, date, order or
    chamber. The change list is the audit record (no second label is stored): it only takes its name after the database
    commit, and an existing list is never overwritten. `--undo LIST --commit` writes each listed item's old labels back,
    if it still holds the new ones.

    A bill whose action labels change has its computed fields recomputed by `update_bill_fields` (`latest_passage_date`
    reads `passage`; the other three are rewritten too if an older import left them stale) and its `updated_at` moved,
    as a re-import of the same change would, so api-v3's `updated_since` readers (the broker's bill-event check,
    ddp-sync) see it: hence the silent window. Both the dry run and the commit report how many bills each computed
    field changes on (OPEN-615).

    A bill whose own label changes and none of whose actions' labels do is written **without** moving `updated_at`, as
    `relabel-non-bills` does (when its actions change too, `updated_at` moves as above). That label is stored
    downstream: ddp-sync copies a bill's `classification` into its knowledge-base text ("Type:",
    `ingestion/sources/openstates.py`) and metadata (`ingestion/metadata.py`) when it ingests the bill, and finds
    changed bills through `updated_since`. So ddp-sync keeps the old type for these
    bills until their next real update; a state ticket that changes bill labels (Virginia's, OPEN-545) says how that
    copy is refreshed.
    """
    from openstates.ddp_states import state_rule

    if commit and not list_path:
        raise click.ClickException(
            "--commit needs --list FILE: that file is the record of what changed"
        )
    if commit and (os.path.exists(list_path) or os.path.exists(f"{list_path}.partial")):
        raise click.ClickException(
            f"{list_path} (or its .partial) already exists: an earlier record is never overwritten; nothing written"
        )
    classify_bill = classify_action = None
    if not undo_path:
        classify_bill = state_rule(state, "classify_bill")
        classify_action = state_rule(state, "classify_action")
        if classify_bill is None and classify_action is None:
            raise click.ClickException(
                f"{state}: openstates/ddp_states/{state.lower()}.py has no classify_action or classify_bill; "
                "there is no rule to re-apply"
            )

    init_django()
    # Imported here, after init_django(), as in reverse-action-order.
    from ..importers.computed_fields import update_bill_fields
    from openstates.data.models import Bill, BillAction

    bills = Bill.objects.filter(legislative_session__jurisdiction_id=abbr_to_jid(state))
    if session:
        bills = bills.filter(legislative_session__identifier=session)

    if undo_path:
        changes = _undo_plan(bills, undo_path)
        unchanged = None
    else:
        changes, unchanged = _relabel_plan(bills, classify_bill, classify_action)

    # Written aside and named only after the commit, as in reverse-action-order: a refused or rolled-back run leaves
    # no list that reads as done.
    written_path = f"{list_path}.partial" if commit and list_path else list_path
    field_changes = _computed_field_preview(changes) if not commit else dict.fromkeys(COMPUTED_BILL_FIELDS, 0)
    try:
        if list_path:
            with open(written_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(RELABEL_LIST_COLUMNS)
                for change in changes:
                    writer.writerow(
                        [
                            ";".join(change[c]) if c in ("old", "new") else change[c]
                            for c in RELABEL_LIST_COLUMNS
                        ]
                    )
        if commit:
            if expect is not None and expect != len(changes):
                raise click.ClickException(
                    f"{len(changes)} items would change, not the expected {expect}; nothing written"
                )
            with transaction.atomic():
                # Lock what is written (an import of one of these bills waits), then write each item only if its
                # labels are still the ones read: anything else aborts and rolls back the whole run.
                touched_bills = sorted({c["bill_id"] for c in changes})
                list(Bill.objects.select_for_update().filter(id__in=touched_bills).values_list("id", flat=True))
                list(
                    BillAction.objects.select_for_update()
                    .filter(id__in=[c["id"] for c in changes if c["kind"] == "action"])
                    .values_list("id", flat=True)
                )
                for change in changes:
                    _write_if_unchanged(
                        Bill if change["kind"] == "bill" else BillAction,
                        change["id"],
                        # the rule's inputs too: a row whose text changed since it was read was labelled from old text
                        {"classification": change["old"], **change["read"]},
                        {"classification": change["new"]},
                        f"{change['kind']} {change['id']} of {change['identifier']}",
                    )
                for bill in Bill.objects.filter(
                    id__in={c["bill_id"] for c in changes if c["kind"] == "action"}
                ):
                    before = {f: getattr(bill, f) for f in COMPUTED_BILL_FIELDS}
                    update_bill_fields(bill)  # saves when a computed field changed
                    if not _changed_fields(before, bill, field_changes):
                        bill.save(update_fields=["updated_at"])
    except BaseException:
        if commit and list_path and os.path.exists(written_path):
            os.remove(written_path)
        raise
    if commit:
        try:
            os.replace(written_path, list_path)
        except OSError as e:
            raise click.ClickException(
                f"{state}: COMMITTED, but the change list could not take its name ({e}); "
                f"it is complete at {written_path}. Do not run again: the commit is done"
            )

    mode = "COMMITTED" if commit else "DRY RUN"
    color = "green" if commit else "yellow"
    word = "restored" if undo_path else "changed"
    counts = []
    for kind in ("action", "bill"):
        n = sum(1 for c in changes if c["kind"] == kind)
        line = f"{kind}s {word}={n}"
        if unchanged is not None:
            line += f" unchanged={unchanged[kind]}"
        counts.append(line)
    click.secho(f"{state}: [{mode}] " + " | ".join(counts), fg=color)
    for kind in ("action", "bill"):
        for line in _label_change_counts((c["old"], c["new"]) for c in changes if c["kind"] == kind):
            click.secho(f"  {kind}:{line[1:]}", fg=color)
    click.secho(
        f"  bills whose computed fields {'changed' if commit else 'would change'}: "
        + " ".join(f"{f}={field_changes[f]}" for f in COMPUTED_BILL_FIELDS),
        fg=color,
    )
    if commit:
        click.secho(f"  change list: {list_path}", fg=color)


def _date_direction(dates: typing.List[str]) -> str:
    """How a bill's stored action dates run, in stored order (day only: one source stamps a time, one doesn't)."""
    days = [d[:10] for d in dates]
    ups = any(b > a for a, b in zip(days, days[1:]))
    downs = any(b < a for a, b in zip(days, days[1:]))
    if ups and downs:
        return "mixed"
    if downs:
        return "newest_first"
    if ups:
        return "oldest_first"
    return "same_day"


@main.command(
    help="OPEN-541: reverse the stored action order of every bill of a state whose actions are stored newest "
    "first (dry run by default; --commit writes, with --list naming the file that records every change)"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="write the new order; default is a dry run that only reports counts",
)
@click.option(
    "--undo",
    is_flag=True,
    default=False,
    help="reverse again every bill an earlier --commit reversed (rollback inside the silent window)",
)
@click.option(
    "--expect",
    type=int,
    default=None,
    help="abort before writing unless exactly this many bills would change (the agreed dry-run count)",
)
@click.option(
    "--list",
    "list_path",
    default=None,
    help="write one CSV row per bill that would change (old and new action order); required with --commit",
)
def reverse_action_order(
    state: str,
    session: str = None,
    commit: bool = False,
    undo: bool = False,
    expect: int = None,
    list_path: str = None,
) -> None:
    """
    Plan PLAN-state-specific-handling §6 Phase 2: once a state's scraper stores actions oldest first, its stored
    history is reversed in the same silent window, so one state never holds both orders.

    It reads no per-state label (OPEN-544 removed the manifest's `actions.newest_first` and this command's check of
    it): the bill's own dates and its mark decide. The dry run's date-direction counts are the evidence a state is
    stored newest first, and `--expect` holds the commit to that count.

    A bill is reversed unless it already carries `ACTIONS_REVERSED_KEY` (an earlier commit reversed it), has fewer
    than two actions, or its dates already run oldest first. Same-day and mixed-date bills follow the state's
    order, so they are reversed and counted apart. The same set of `order` values is kept, handed out in reverse.
    The bill's computed action fields are then recomputed the way an import does (`update_bill_fields`); a bill
    whose latest action changes is saved, which moves its `updated_at`, exactly as a re-import would. The marker
    itself is written by a queryset update and moves nothing. `--undo` is the rollback inside the same silent
    window (scrape paused): it reverses each marked bill's actions as they stand, so it is not for use after a
    re-scrape.
    """
    if commit and not list_path:
        raise click.ClickException(
            "--commit needs --list FILE: that file is the record of what changed"
        )
    init_django()
    # Imported here, after init_django(): the importers load the Django models at import time, and a module-level
    # import made every os-text-extract command (archive included) fail before Django was set up.
    from ..importers.computed_fields import update_bill_fields
    from openstates.data.models import Bill, BillAction

    bills = Bill.objects.filter(legislative_session__jurisdiction_id=abbr_to_jid(state))
    if session:
        bills = bills.filter(legislative_session__identifier=session)
    bills = bills.select_related("legislative_session").order_by("id")

    changes = []  # (bill, actions in stored order, new extras)
    skipped: dict[str, int] = {}
    shapes: dict[str, int] = {}
    for bill in bills:
        extras = dict(bill.extras or {})
        if undo:
            if not extras.pop(ACTIONS_REVERSED_KEY, None):
                continue
        elif extras.get(ACTIONS_REVERSED_KEY):
            skipped["already reversed"] = skipped.get("already reversed", 0) + 1
            continue
        actions = list(BillAction.objects.filter(bill=bill).order_by("order", "id"))
        if not undo:
            if len(actions) < 2:
                skipped["single action"] = skipped.get("single action", 0) + 1
                continue
            shape = _date_direction([a.date for a in actions])
            if shape == "oldest_first":
                skipped["already oldest first"] = (
                    skipped.get("already oldest first", 0) + 1
                )
                continue
            shapes[shape] = shapes.get(shape, 0) + 1
            extras[ACTIONS_REVERSED_KEY] = True
        changes.append((bill, actions, extras))

    # A commit's list is the audit record, so it is written aside and only takes its name once the transaction has
    # committed: a refused or rolled-back run leaves no list that reads as done.
    written_path = f"{list_path}.partial" if commit and list_path else list_path
    if list_path:
        with open(written_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "bill_id",
                    "identifier",
                    "session",
                    "actions",
                    "old_order",
                    "new_order",
                    "old_dates",
                    "new_dates",
                ]
            )
            for bill, actions, _ in changes:
                writer.writerow(
                    [
                        bill.id,
                        bill.identifier,
                        bill.legislative_session.identifier,
                        len(actions),
                        ";".join(str(a.id) for a in actions),
                        ";".join(str(a.id) for a in reversed(actions)),
                        ";".join(a.date for a in actions),
                        ";".join(a.date for a in reversed(actions)),
                    ]
                )

    latest_changed = 0
    if commit:
        try:
            if expect is not None and expect != len(changes):
                raise click.ClickException(
                    f"{len(changes)} bills would change, not the expected {expect}; nothing written"
                )
            with transaction.atomic():
                for bill, actions, extras in changes:
                    # Write only if the bill is still what was read: same actions in the same order, same extras. An
                    # import landing mid-run aborts the whole run instead of being overwritten. The re-read locks the
                    # action rows until the commit, so an import (which deletes and recreates a bill's actions) waits.
                    read = [(a.id, a.order) for a in actions]
                    now = list(
                        BillAction.objects.select_for_update()
                        .filter(bill=bill)
                        .order_by("order", "id")
                        .values_list("id", "order")
                    )
                    marked = Bill.objects.filter(pk=bill.pk, extras=bill.extras).update(
                        extras=extras
                    )
                    if now != read or marked != 1:
                        raise click.ClickException(
                            f"{bill.id} ({bill.identifier}) changed since it was read; "
                            "rolled back, nothing written"
                        )
                    orders = [a.order for a in actions]
                    for action, order in zip(reversed(actions), orders):
                        action.order = order
                    BillAction.objects.bulk_update(actions, ["order"])
                    fresh = Bill.objects.get(pk=bill.pk)
                    before = (
                        fresh.first_action_date,
                        fresh.latest_action_date,
                        fresh.latest_action_description,
                        fresh.latest_passage_date,
                    )
                    update_bill_fields(fresh, save=True)
                    if before != (
                        fresh.first_action_date,
                        fresh.latest_action_date,
                        fresh.latest_action_description,
                        fresh.latest_passage_date,
                    ):
                        latest_changed += 1
        except BaseException:
            os.remove(written_path)
            raise
        try:
            os.replace(written_path, list_path)
        except OSError as e:
            raise click.ClickException(
                f"{state}: COMMITTED, but the change list could not take its name ({e}); "
                f"it is complete at {written_path}. Do not run again: the commit is done"
            )

    mode = "COMMITTED" if commit else "DRY RUN"
    color = "green" if commit else "yellow"
    action_word = "restored" if undo else "reversed"
    line = (
        f"{state}: [{mode}] {action_word}={len(changes)} "
        f"actions={sum(len(a) for _, a, _ in changes)} "
        + " ".join(
            f"skipped_{k.replace(' ', '_')}={v}" for k, v in sorted(skipped.items())
        )
    )
    click.secho(line.rstrip(), fg=color)
    if shapes:
        click.secho(
            "  stored dates: "
            + " ".join(f"{k}={v}" for k, v in sorted(shapes.items())),
            fg=color,
        )
    if commit:
        click.secho(
            f"  latest action fields recomputed and changed: {latest_changed}; change list: {list_path}",
            fg=color,
        )


def _stale_document_plan(bills: typing.Any) -> tuple:
    """
    OPEN-564: which saved documents of `bills` are stale copies that can go. A document is stale when its version
    note and date are no longer a version of its bill (the archiver keys documents on the note and date, so a renamed
    or newly dated version is saved again and the old row stays behind; the date since OPEN-569). A stale document is removed only when a
    document that is NOT stale keeps the same file: same bill, same address, same `sha256_hash`. A stale document
    without such a copy is kept and counted, so the last saved copy of a file is never removed.

    Returns (rows read, the removals as (stale row, kept copy) value dicts in id order, counts of kept stale rows).
    """
    from openstates.data.models import BillVersion, BillVersionDocument

    # OPEN-569: keyed on the date too, like the archive: when the scraper dates a version, its undated copy is stale
    current = set(
        BillVersion.objects.filter(bill__in=bills).values_list("bill_id", "note", "date")
    )
    rows = list(
        BillVersionDocument.objects.filter(bill__in=bills)
        .order_by("id")
        .values(
            "id",
            "bill_id",
            "bill__identifier",
            "version_note",
            "version_date",
            "source_url",
            "sha256_hash",
            "is_bill_version",
            "archive_location",
        )
    )
    kept_copy: dict[tuple, dict] = {}
    for row in rows:
        if (row["bill_id"], row["version_note"], row["version_date"]) in current and row["sha256_hash"]:
            kept_copy.setdefault(
                (row["bill_id"], row["source_url"], row["sha256_hash"]), row
            )
    removals = []
    kept = {"last copy": 0, "no file hash": 0}
    for row in rows:
        if (row["bill_id"], row["version_note"], row["version_date"]) in current:
            continue
        copy = kept_copy.get((row["bill_id"], row["source_url"], row["sha256_hash"]))
        if not row["sha256_hash"]:
            kept["no file hash"] += 1
        elif copy is None:
            kept["last copy"] += 1
        else:
            removals.append((row, copy))
    return rows, removals, kept


@main.command(
    help="OPEN-564: remove a state's saved documents whose version note and date no longer exist on the bill, only where "
    "a current document keeps the same file (dry run by default; --commit writes, with --list naming the file that "
    "records every removal)"
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="remove the rows; default is a dry run that only reports counts",
)
@click.option(
    "--expect",
    type=int,
    default=None,
    help="abort before writing unless exactly this many rows would be removed (the agreed dry-run count)",
)
@click.option(
    "--list",
    "list_path",
    default=None,
    help="write one CSV row per document that would be removed, with the copy that stays; required with --commit",
)
def remove_stale_documents(
    state: str,
    session: str = None,
    commit: bool = False,
    expect: int = None,
    list_path: str = None,
) -> None:
    """
    Alabama, 2026-11-15: OPEN-528 reversed the action order, the scraper names each amendment or substitute paper
    after the first action that carries it, so 456 papers got a new version note and the archive saved each one a
    second time. The old rows match no version any more. This removes them, in one transaction, re-reading the plan
    inside it and aborting if anything changed since the dry run. The files themselves are untouched: the kept row
    has the same bytes (`sha256_hash`) and the archive copy in S3 stays where it is. Run `recompute-diff-order` on
    the state afterwards, so every kept document's diff is taken against its real neighbours.
    """
    if commit and not list_path:
        raise click.ClickException(
            "--commit needs --list FILE: that file is the record of what was removed"
        )
    if commit and (os.path.exists(list_path) or os.path.exists(f"{list_path}.partial")):
        raise click.ClickException(
            f"{list_path} (or its .partial) already exists: an earlier record is never overwritten; nothing written"
        )

    init_django()
    from openstates.data.models import Bill, BillVersion, BillVersionDocument

    bills = Bill.objects.filter(legislative_session__jurisdiction_id=abbr_to_jid(state))
    if session:
        bills = bills.filter(legislative_session__identifier=session)

    rows, removals, kept = _stale_document_plan(bills)
    stale = len(removals) + sum(kept.values())

    # Written aside and named only after the commit, as in reverse-action-order: a refused or rolled-back run leaves
    # no list that reads as done.
    written_path = f"{list_path}.partial" if commit and list_path else list_path
    if list_path:
        with open(written_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "removed_id",
                    "bill_id",
                    "identifier",
                    "removed_note",
                    "version_date",
                    "source_url",
                    "sha256_hash",
                    "removed_is_bill_version",
                    "archive_location",
                    "kept_id",
                    "kept_note",
                ]
            )
            for row, copy in removals:
                writer.writerow(
                    [
                        row["id"],
                        row["bill_id"],
                        row["bill__identifier"],
                        row["version_note"],
                        row["version_date"],
                        row["source_url"],
                        row["sha256_hash"],
                        row["is_bill_version"],
                        row["archive_location"] or "",
                        copy["id"],
                        copy["version_note"],
                    ]
                )

    if commit:
        try:
            if expect is not None and expect != len(removals):
                raise click.ClickException(
                    f"{len(removals)} rows would be removed, not the expected {expect}; nothing written"
                )
            with transaction.atomic():
                # Lock the state's documents and versions (an import replaces a bill's versions by deleting them, so it
                # waits), then plan again: an archive or import landing since the read aborts the whole run instead of
                # removing a row that is no longer stale or whose copy is gone. The plan compared is the whole of what
                # the list records, so the list describes exactly what was removed.
                for model in (BillVersionDocument, BillVersion):
                    list(
                        model.objects.select_for_update()
                        .filter(bill__in=bills)
                        .values_list("id", flat=True)
                    )
                _, again, _ = _stale_document_plan(bills)
                if again != removals:
                    raise click.ClickException(
                        f"{state}: the documents changed since they were read; rolled back, nothing written"
                    )
                removed_ids = [r["id"] for r, _ in removals]
                # OPEN-539: a comparison taken against a removed row no longer names a stored document; clear it
                # (the recompute this command asks for afterwards restamps it). Baselines never cross bills.
                BillVersionDocument.objects.filter(
                    bill__in=bills, diff_baseline_id__in=removed_ids
                ).update(diff_baseline_id=None)
                deleted, _ = BillVersionDocument.objects.filter(
                    id__in=removed_ids
                ).delete()
                if deleted != len(removals):
                    raise click.ClickException(
                        f"{state}: removed {deleted}, not {len(removals)}; rolled back, nothing written"
                    )
        except BaseException:
            if list_path:
                os.remove(written_path)
            raise
        try:
            os.replace(written_path, list_path)
        except OSError as e:
            raise click.ClickException(
                f"{state}: COMMITTED, but the change list could not take its name ({e}); "
                f"it is complete at {written_path}. Do not run again: the commit is done"
            )

    remaining = len(rows) - len(removals)
    distinct_files = len({(r["bill_id"], r["source_url"]) for r in rows})
    mode = "COMMITTED" if commit else "DRY RUN"
    color = "green" if commit else "yellow"
    click.secho(
        f"{state}: [{mode}] documents={len(rows)} stale={stale} removed={len(removals)} "
        + " ".join(
            f"kept_{k.replace(' ', '_')}={v}" for k, v in sorted(kept.items())
        ),
        fg=color,
    )
    click.secho(
        f"  after: documents={remaining} distinct (bill, address)={distinct_files} "
        f"removed_versions={sum(1 for r, _ in removals if r['is_bill_version'])}",
        fg=color,
    )
    if commit:
        click.secho(f"  change list: {list_path}", fg=color)


_s3_client_cache = None


def _cached_s3_client():
    """`_get_s3_client()` builds a fresh `boto3.client("s3")` (real credential resolution,
    config file reads) on every call -- fine for `_upload_and_verify_direct()`'s one-call-per-
    document-at-archive-time use, but `_fetch_archive_bytes()` below can call it once per stale
    document in a single run, potentially thousands of times on a host with no local mirror.
    Cached here, scoped to this module's own read path only; `_upload_and_verify_direct()` is
    left calling `_get_s3_client()` directly, unchanged -- fixing its own call pattern is a
    separate concern this PR isn't taking on."""
    global _s3_client_cache
    if _s3_client_cache is None:
        _s3_client_cache = _get_s3_client()
    return _s3_client_cache


def _fetch_archive_bytes(
    rel_path: str, local_path: str
) -> typing.Tuple[typing.Optional[bytes], typing.Optional[str]]:
    """Read one archived document's raw bytes, local disk first, S3 GetObject on a local miss.

    Found needing this 2026-09-04: the EC2 host running `refresh-extraction` against RDS has no
    local `ARCHIVE_ROOT_DIR` mirror at all (`cloud_archiver.py` writes locally on the Mac and
    only mirrors to S3 -- nothing ever populated a copy on this host), so local-only reads
    reported every single document "not attempted" for three whole jurisdictions rather than
    genuinely finding them clean. `_get_s3_client()`/`S3_BILL_ARCHIVE_BUCKET` already exist for
    the archive *write* side (`_upload_and_verify_direct`) -- this reuses both rather than
    requiring a bulk local sync or moving the run to a different host, since only the
    documents genuinely stale need fetching at all, not the whole archive up front.

    Returns `(data, None)` on success from either source, or `(None, reason)` if both the
    local file and the S3 object are unavailable.

    `reason` deliberately does NOT include `local_path` or any other per-document detail
    (pm-review, round 1: an earlier version did, which defeats `refresh_extraction`'s own
    `skip_reasons` dict -- it groups by exact string match to surface the most common failure,
    and a reason unique to every document, however descriptive, can never group with anything,
    so a systemic failure -- wrong credentials, network down -- would report as dozens of
    "different" one-count reasons instead of one big, visible one). A genuinely-missing object
    (`NoSuchKey`/404), an object sitting unrestored in Glacier Deep Archive
    (`InvalidObjectState` -- see below), and any other S3-side failure (auth, throttling,
    network) are each reported under their own, still-poolable string, so any one class
    dominates the report's existing top-10-by-count display on its own rather than blurring
    together.

    Deliberately does not call `RestoreObject` or retry after one for a Deep-Archive object:
    the archive has two storage tiers (`_upload_and_verify_via_wrapper`'s original path writes
    to Glacier Deep Archive; `_upload_and_verify_direct`'s newer cloud path, OPEN-192, writes at
    GLACIER_IR specifically to be immediately readable, no restore needed), and a ~12h
    asynchronous restore-then-reprocess workflow is a different, stateful feature this function
    should not attempt silently as a side effect of a read.
    """
    try:
        with open(local_path, "rb") as f:
            return f.read(), None
    except FileNotFoundError:
        pass

    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client = _cached_s3_client()
        obj = client.get_object(Bucket=S3_BILL_ARCHIVE_BUCKET, Key=rel_path)
        return obj["Body"].read(), None
    except ClientError as e:
        if hasattr(e, "response"):
            code = e.response.get("Error", {}).get("Code", "")
        else:
            code = ""
        if code in ("NoSuchKey", "404"):
            return None, "not found locally or in S3"
        if code == "InvalidObjectState":
            # A real, expected, per-document condition, not a systemic problem -- the archive
            # has two storage tiers (`_upload_and_verify_via_wrapper`'s original path writes to
            # Glacier Deep Archive; `_upload_and_verify_direct`'s newer cloud path, OPEN-192,
            # writes at GLACIER_IR, which never raises this -- GLACIER_IR reads like a normal
            # GetObject, no restore ever needed). A plain GetObject against a Deep Archive
            # object that was never explicitly restored raises
            # exactly this code -- distinct from both "genuinely missing" and "systemic S3
            # trouble," so it gets its own poolable reason rather than folding into either.
            # This function deliberately does NOT call RestoreObject or retry after one --
            # a ~12h asynchronous restore-then-reprocess workflow is a different, stateful
            # feature, not something a synchronous per-document read should attempt silently.
            return None, "archived in Glacier Deep Archive, needs restore (~12h) first"
        reason = (
            f"S3 error ({code or 'ClientError'}) -- may be systemic, not per-document"
        )
        return None, reason
    except BotoCoreError as e:
        reason = f"S3 connection/config error ({type(e).__name__}) -- may be systemic"
        return None, reason


def _reextract_document(doc: typing.Any) -> dict[str, typing.Any]:
    """
    Re-run text extraction for one already-archived `BillVersionDocument`, reading its raw
    bytes from the local archive copy on `/Volumes/DDP-HOT` where one exists, falling back to
    the S3 archive (`_fetch_archive_bytes`) where it doesn't -- no re-fetching from the live
    site regardless. Same "reprocess in place" approach OPEN-33 used for its VA backfill,
    generalized here (OPEN-49) so it isn't a one-off script per jurisdiction.

    Returns a dict with keys: "attempted" (bool -- False means neither local disk nor S3 had
    the bytes, so the row wasn't touched at all), "new_raw_text", "new_is_error", "reason" (set
    on any non-fatal skip/failure, for the dry-run report).
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
    data, fetch_failure = _fetch_archive_bytes(rel_path, local_path)
    if data is None:
        return {"attempted": False, "reason": fetch_failure}

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
        new_is_error = not _has_text(new_raw_text)
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
    help="re-run text extraction for already-archived (but errored) bill documents, reading "
    "the local archive copy where one exists, S3 on a local miss -- never re-fetches from the "
    "live site (OPEN-49)"
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


@main.command(
    name="refresh-extraction",
    help="OPEN-211: re-extract already-archived documents whose stored raw_text no longer "
    "matches what the current extractor produces, then recompute that bill's diffs",
)
@click.argument("state")
@click.option("--session", default=None)
@click.option(
    "--commit/--dry-run",
    default=False,
    help="apply changes to the DB; default is a dry run that only reports counts",
)
@click.option("-n", default=None, help="limit number of bills processed, for testing")
def refresh_extraction(
    state: str, session: str = None, commit: bool = False, n: int = None
) -> None:
    """
    Makes an extractor fix retroactive.

    `reextract` cannot do this: it selects `is_error=True`, and the documents this exists for
    extracted "successfully" -- as a single line. ~51,900 of them: 43,055 US XML, 5,750
    Washington HTML, 3,100 Utah XML (OPEN-210, OPEN-212).

    Nor is re-extraction on its own enough. `archive_bill_versions()` skips an already-archived
    document and feeds its STORED text into the next version's diff, so a freshly-extracted
    version is compared against its predecessor's old single-line text and the result is as
    degenerate as before. Verified on Utah SB 0059: fresh-vs-fresh gives 5 hunks / 7,585 chars,
    fresh-vs-stored gives one hunk of 20,560.

    So this walks BILL BY BILL and, for each: re-extracts every document whose current
    extractor output differs from what is stored, and only then recomputes that bill's diffs.
    The ordering is structural rather than a rule to remember -- recomputing while any version
    of the bill still holds stale text simply reproduces the problem for that hop.

    Reads bytes via `_reextract_document` (local archive copy first, S3 on a local miss --
    `_fetch_archive_bytes`), so there is no re-fetching from any legislature's site regardless
    of which of those two sources actually has the bytes.

    Idempotent: a second run finds nothing stale and rewrites nothing.

    Dry run reports how many documents are stale. It deliberately does NOT predict the diff
    recompute, because `recompute_bill_diff_order` reads `raw_text` from the database -- with
    nothing committed it would be recomputing against the stale text, and reporting that number
    would be worse than reporting none.
    """
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
    bills = bills.filter(version_documents__isnull=False).distinct()
    if n:
        bills = bills[: int(n)]

    bills_touched = 0
    docs_stale = 0
    docs_skipped = 0
    docs_refused = 0
    diffs_corrected = 0
    diffs_would_change = 0
    skip_reasons: dict[str, int] = {}
    refused_reasons: dict[str, int] = {}

    class _Proposed:
        """Stand-in carrying a document's PROPOSED text, for simulating the diff recompute in a
        dry run without writing anything. `recomputed_diffs_for_documents` reads only these
        attributes, which is why it was split out from its database-querying caller."""

        __slots__ = (
            "version_note", "version_date", "source_url", "media_type",
            "raw_text", "is_error", "diff_from_previous_version",
        )

        def __init__(self, doc, raw_text, is_error):
            self.version_note = doc.version_note
            self.version_date = doc.version_date
            self.source_url = doc.source_url
            self.media_type = doc.media_type
            self.raw_text = raw_text
            self.is_error = is_error
            self.diff_from_previous_version = doc.diff_from_previous_version

    for bill in bills:
        proposed = []
        stale_docs = []
        for doc in bill.version_documents.all():
            result = _reextract_document(doc)
            if not result["attempted"]:
                docs_skipped += 1
                reason = result["reason"]
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue

            # Strict, not `.get(..., "")`. _reextract_document has an attempted=True branch that
            # returns NEITHER key (the DoNotDownload media types) and another that returns
            # new_is_error=True (an extractor exception). Defaulting those to empty text and
            # is_error=True would overwrite a perfectly good stored extraction with nothing --
            # real, reachable data loss on a ~51,900-document migration. Raised by /pm-review.
            if "new_raw_text" not in result or "new_is_error" not in result:
                docs_skipped += 1
                reason = result.get("reason", "helper returned no extraction")
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue
            new_raw_text = result["new_raw_text"]
            new_is_error = result["new_is_error"]

            # Never downgrade. This command exists to give documents BETTER text; a document
            # that currently extracts worse than what is stored is a signal to look at, not a
            # row to overwrite.
            currently_good = _has_text(doc.raw_text) and not doc.is_error
            now_worse = new_is_error or not _has_text(new_raw_text)
            if currently_good and now_worse:
                docs_refused += 1
                # result["reason"] is None on the non-exception "extracted to empty" path (see
                # _reextract_document) -- distinguished from an actual raised exception so the
                # two don't silently merge into one bucket and hide which one actually dominates.
                reason = result.get("reason") or "extraction returned empty, no exception raised"
                refused_reasons[reason] = refused_reasons.get(reason, 0) + 1
                continue

            if new_raw_text == doc.raw_text and new_is_error == doc.is_error:
                proposed.append(_Proposed(doc, doc.raw_text, doc.is_error))
                continue
            docs_stale += 1
            stale_docs.append((doc, new_raw_text, new_is_error))
            proposed.append(_Proposed(doc, new_raw_text, new_is_error))

        if not stale_docs:
            continue
        bills_touched += 1

        if not commit:
            # Simulate the recompute against the PROPOSED text, so a dry run reports the thing
            # this migration is actually for rather than only how many documents would change.
            diffs_would_change += len(
                recomputed_diffs_for_documents(
                    proposed,
                    jurisdiction_name=bill.legislative_session.jurisdiction.name,
                    is_bill=bill.classification == ["bill"],
                    bill_identifier=bill.identifier,
                    current_versions=_current_version_keys(bill),
                )["changed"]
            )
            continue

        # One transaction per bill. The ordering below -- every version brought current before
        # any diff is recomputed -- is only an invariant if it cannot be interrupted halfway,
        # which would leave refreshed text paired with diffs computed from the old text.
        with transaction.atomic():
            for doc, new_raw_text, new_is_error in stale_docs:
                doc.raw_text = new_raw_text
                doc.is_error = new_is_error
                doc.save(update_fields=["raw_text", "is_error", "updated_at"])

            result = recompute_bill_diff_order(bill)
            for doc, new_diff in result["changed"]:
                doc.diff_from_previous_version = new_diff
                doc.save(update_fields=["diff_from_previous_version", "updated_at"])
                diffs_corrected += 1
            # OPEN-539: each stored comparison names the document it was taken against. Only the id
            # is written here, so a document whose comparison did not change keeps its updated_at.
            for doc, _, new_id, _ in _baseline_changes(result, diffs_written=True)[0]:
                doc.diff_baseline_id = new_id
                doc.save(update_fields=["diff_baseline_id"])

    mode = "COMMITTED" if commit else "DRY RUN"
    click.secho(
        f"{state}: [{mode}] bills_with_stale_docs={bills_touched} "
        f"stale_docs={docs_stale} "
        f"{'diffs_corrected' if commit else 'diffs_would_change'}="
        f"{diffs_corrected if commit else diffs_would_change} "
        f"docs_skipped={docs_skipped} docs_refused={docs_refused}",
        fg="green" if commit else "yellow",
    )
    if docs_refused:
        click.secho(
            f"  refused {docs_refused} document(s): stored text is good but the current "
            "extractor returns empty/errored output -- not overwritten, worth investigating",
            fg="red",
        )
        for reason, count in sorted(refused_reasons.items(), key=lambda kv: -kv[1])[:10]:
            click.secho(f"  refused {count}: {reason}", fg="red")
    if skip_reasons:
        for reason, count in sorted(skip_reasons.items(), key=lambda kv: -kv[1])[:10]:
            click.secho(f"  skipped {count}: {reason}", fg="yellow")
