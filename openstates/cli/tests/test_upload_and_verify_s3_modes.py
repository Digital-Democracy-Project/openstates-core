"""OPEN-192 (Phase 3, scraper-execution migration): `_upload_and_verify` gained a second
transport -- `ARCHIVE_S3_MODE=direct`, an ordinary boto3 PutObject for running this same
archive code from a cloud container that has no sudo and no wrapper binary at all, alongside
the original `ARCHIVE_S3_MODE=wrapper` (default) Mac path, unchanged.

No Django/DB fixtures needed here -- these are pure S3-transport and verification-logic tests,
deliberately kept in their own file rather than added to test_text_extract.py's Django-backed
suite, matching this directory's existing split (test_archive_concurrent_writes.py,
test_archive_per_media.py) by concern rather than by which module the code lives in.
"""

import hashlib
import os
from unittest import mock

import pytest  # type: ignore
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from openstates.cli.text_extract import (
    _check_etag,
    _upload_and_verify,
    _upload_and_verify_direct,
    S3_BILL_ARCHIVE_BUCKET,
)


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "PutObject")


class TestCheckEtag:
    """The verification rule both transports share -- see its own docstring for why a single
    shared function exists at all."""

    def test_matching_single_part_etag_verifies(self):
        assert _check_etag("abc123", "abc123", "some/key") is True

    def test_missing_etag_fails(self):
        assert _check_etag("", "abc123", "some/key") is False
        assert _check_etag(None, "abc123", "some/key") is False

    def test_multipart_etag_fails_even_if_it_happens_to_contain_the_hash(self):
        # A "-N" suffix means "hash of part-hashes", not a plain MD5 -- not comparable to
        # local_md5 at all, regardless of what the prefix looks like.
        assert _check_etag("abc123-2", "abc123-2", "some/key") is False

    def test_mismatched_etag_fails(self):
        assert _check_etag("abc123", "def456", "some/key") is False


class TestUploadAndVerifyDispatch:
    """`_upload_and_verify` itself does nothing but read ARCHIVE_S3_MODE and dispatch --
    verifying that dispatch is correct is independent of either transport's own internals,
    which get their own test classes below."""

    def test_default_mode_calls_wrapper_path(self, monkeypatch):
        monkeypatch.delenv("ARCHIVE_S3_MODE", raising=False)
        with mock.patch(
            "openstates.cli.text_extract._upload_and_verify_via_wrapper",
            return_value="s3://wrapper-result",
        ) as wrapper, mock.patch(
            "openstates.cli.text_extract._upload_and_verify_direct"
        ) as direct:
            result = _upload_and_verify("/tmp/x", "some/key", "abc123")
        assert result == "s3://wrapper-result"
        wrapper.assert_called_once_with("/tmp/x", "some/key", "abc123")
        direct.assert_not_called()

    def test_explicit_wrapper_mode_calls_wrapper_path(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_S3_MODE", "wrapper")
        with mock.patch(
            "openstates.cli.text_extract._upload_and_verify_via_wrapper",
            return_value="s3://wrapper-result",
        ) as wrapper:
            result = _upload_and_verify("/tmp/x", "some/key", "abc123")
        assert result == "s3://wrapper-result"
        wrapper.assert_called_once()

    def test_direct_mode_calls_direct_path(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_S3_MODE", "direct")
        with mock.patch(
            "openstates.cli.text_extract._upload_and_verify_direct",
            return_value="s3://direct-result",
        ) as direct, mock.patch(
            "openstates.cli.text_extract._upload_and_verify_via_wrapper"
        ) as wrapper:
            result = _upload_and_verify("/tmp/x", "some/key", "abc123")
        assert result == "s3://direct-result"
        direct.assert_called_once_with("/tmp/x", "some/key", "abc123")
        wrapper.assert_not_called()

    def test_unrecognized_mode_fails_closed_instead_of_falling_back_to_wrapper(
        self, monkeypatch
    ):
        # A typo'd ARCHIVE_S3_MODE (e.g. "driect") must not silently invoke the sudo-gated Mac
        # wrapper -- that binary doesn't exist in a cloud container, so falling back to it would
        # trade one clear failure for a confusing one deeper in the stack.
        monkeypatch.setenv("ARCHIVE_S3_MODE", "driect")
        with mock.patch(
            "openstates.cli.text_extract._upload_and_verify_via_wrapper"
        ) as wrapper, mock.patch(
            "openstates.cli.text_extract._upload_and_verify_direct"
        ) as direct:
            result = _upload_and_verify("/tmp/x", "some/key", "abc123")
        assert result is None
        wrapper.assert_not_called()
        direct.assert_not_called()


class TestUploadAndVerifyDirect:
    """OPEN-192's new cloud transport. Every test supplies its own real bytes on disk and its
    own real MD5 of them -- matching how the caller (`archive_bill_versions`) actually computes
    `local_md5` -- rather than asserting against a hand-typed hash string."""

    def _write_temp_file(self, tmp_path, content: bytes):
        path = tmp_path / "document.pdf"
        path.write_bytes(content)
        return str(path), hashlib.md5(content).hexdigest()

    def test_missing_working_tier_bucket_fails_closed_without_any_s3_call(
        self, tmp_path, monkeypatch
    ):
        """The one thing this test file exists to prove above all the others: as long as
        OPEN-192's own bucket decision is unmade, direct mode must refuse to archive rather
        than silently writing Deep-Archive-only and calling it done."""
        monkeypatch.delenv("WORKING_TIER_S3_BUCKET", raising=False)
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        with mock.patch("openstates.cli.text_extract._get_s3_client") as get_client:
            result = _upload_and_verify_direct(path, "bills/raw/fl/2026/lower/x.pdf", md5)
        assert result is None
        get_client.assert_not_called()

    def test_successful_upload_writes_both_buckets_and_returns_vault_uri(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        object_key = "bills/raw/fl/2026/lower/x.pdf"
        client = mock.Mock()
        client.head_object.return_value = {"ETag": f'"{md5}"'}
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, object_key, md5)

        assert result == f"s3://{S3_BILL_ARCHIVE_BUCKET}/{object_key}"

        vault_put, working_tier_put = client.put_object.call_args_list
        assert vault_put.kwargs["Bucket"] == S3_BILL_ARCHIVE_BUCKET
        assert vault_put.kwargs["Key"] == object_key
        assert vault_put.kwargs["StorageClass"] == "DEEP_ARCHIVE"
        assert working_tier_put.kwargs["Bucket"] == "ddp-openstates-scraper-memory"
        assert working_tier_put.kwargs["Key"] == object_key
        # The working-tier copy is deliberately NOT Deep Archive -- OPEN-192's whole point is
        # that this copy is immediately readable, no ~12h restore.
        assert "StorageClass" not in working_tier_put.kwargs

    def test_vault_put_object_failure_returns_none_and_never_attempts_working_tier(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.put_object.side_effect = _client_error("AccessDenied")
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None
        assert client.put_object.call_count == 1  # only the vault attempt

    def test_vault_etag_mismatch_returns_none_and_never_attempts_working_tier(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.head_object.return_value = {"ETag": '"not-the-real-md5"'}
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None
        assert client.put_object.call_count == 1  # only the vault attempt

    def test_vault_multipart_etag_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.head_object.return_value = {"ETag": f'"{md5}-2"'}
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None

    def test_working_tier_put_object_failure_returns_none_even_though_vault_already_succeeded(
        self, tmp_path, monkeypatch
    ):
        """The vault write is real and durable at this point -- this test exists to document
        that fact, not to claim it gets rolled back. `archive_location`/`archived_at` still
        must not be set, per this function's own contract: a document unreadable from the
        working tier is not "archived" as OPEN-192 defines the word, even though it exists in
        Deep Archive."""
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.head_object.return_value = {"ETag": f'"{md5}"'}
        client.put_object.side_effect = [None, _client_error("AccessDenied")]
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None
        assert client.put_object.call_count == 2  # vault succeeded, working tier attempted

    def test_working_tier_etag_mismatch_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.head_object.side_effect = [
            {"ETag": f'"{md5}"'},  # vault: matches
            {"ETag": '"wrong"'},  # working tier: does not
        ]
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None

    def test_client_construction_failure_returns_none(self, tmp_path, monkeypatch):
        # _get_s3_client() itself can raise (e.g. ProfileNotFound from a misconfigured
        # AWS_PROFILE) before any put_object/head_object call is even reachable -- this must
        # be caught too, not just failures from calls made on an already-constructed client.
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client",
            side_effect=NoCredentialsError(),
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None

    def test_vault_non_client_botocore_error_returns_none(self, tmp_path, monkeypatch):
        # NoCredentialsError/EndpointConnectionError are BotoCoreError subclasses, not
        # ClientError -- they never got a response from S3 to wrap at all. This function's
        # contract is "None on any failure," so these must be caught too, not just ClientError.
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.put_object.side_effect = NoCredentialsError()
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None

    def test_working_tier_non_client_botocore_error_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        path, md5 = self._write_temp_file(tmp_path, b"pdf bytes")
        client = mock.Mock()
        client.head_object.return_value = {"ETag": f'"{md5}"'}
        client.put_object.side_effect = [
            None,
            EndpointConnectionError(endpoint_url="https://s3.amazonaws.com"),
        ]
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(path, "some/key", md5)
        assert result is None

    def test_unreadable_local_file_returns_none_without_attempting_a_put(self, monkeypatch):
        # _get_s3_client() itself is just object construction (no network call), so it's fine
        # for this to run before the file read fails -- what matters is that no S3 *API call*
        # (put_object) is ever attempted for bytes that were never successfully read.
        monkeypatch.setenv("WORKING_TIER_S3_BUCKET", "ddp-openstates-scraper-memory")
        client = mock.Mock()
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(
                "/nonexistent/path/does/not/exist.pdf", "some/key", "abc123"
            )
        assert result is None
        client.put_object.assert_not_called()
