"""OPEN-192 (Phase 3, scraper-execution migration): `_upload_and_verify` gained a second
transport -- `ARCHIVE_S3_MODE=direct`, an ordinary boto3 PutObject for running this same
archive code from a cloud container that has no sudo and no wrapper binary at all, alongside
the original `ARCHIVE_S3_MODE=wrapper` (default) Mac path, unchanged.

OPEN-235: both transports now take the document's bytes directly instead of a local path --
archive_bill_versions() no longer writes the document to local disk at all before uploading it.

No Django/DB fixtures needed here -- these are pure S3-transport and verification-logic tests,
deliberately kept in their own file rather than added to test_text_extract.py's Django-backed
suite, matching this directory's existing split (test_archive_concurrent_writes.py,
test_archive_per_media.py) by concern rather than by which module the code lives in.
"""

import hashlib
import json
import stat
from unittest import mock

import pytest  # type: ignore
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from openstates.cli.text_extract import (
    _check_etag,
    _upload_and_verify,
    _upload_and_verify_direct,
    _upload_and_verify_via_wrapper,
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
    which get their own test classes below. Uses plain bytes as the payload throughout
    (OPEN-235: this is what the real caller passes now, not a path) -- dispatch doesn't care
    about content, only that it's passed through unchanged."""

    def test_default_mode_calls_wrapper_path(self, monkeypatch):
        monkeypatch.delenv("ARCHIVE_S3_MODE", raising=False)
        with mock.patch(
            "openstates.cli.text_extract._upload_and_verify_via_wrapper",
            return_value="s3://wrapper-result",
        ) as wrapper, mock.patch(
            "openstates.cli.text_extract._upload_and_verify_direct"
        ) as direct:
            result = _upload_and_verify(b"fake bytes", "some/key", "abc123")
        assert result == "s3://wrapper-result"
        wrapper.assert_called_once_with(b"fake bytes", "some/key", "abc123")
        direct.assert_not_called()

    def test_explicit_wrapper_mode_calls_wrapper_path(self, monkeypatch):
        monkeypatch.setenv("ARCHIVE_S3_MODE", "wrapper")
        with mock.patch(
            "openstates.cli.text_extract._upload_and_verify_via_wrapper",
            return_value="s3://wrapper-result",
        ) as wrapper:
            result = _upload_and_verify(b"fake bytes", "some/key", "abc123")
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
            result = _upload_and_verify(b"fake bytes", "some/key", "abc123")
        assert result == "s3://direct-result"
        direct.assert_called_once_with(b"fake bytes", "some/key", "abc123")
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
            result = _upload_and_verify(b"fake bytes", "some/key", "abc123")
        assert result is None
        wrapper.assert_not_called()
        direct.assert_not_called()


class TestUploadAndVerifyDirect:
    """OPEN-192's cloud transport, corrected 2026-08-31 (OPEN-238) to a single write: no more
    separate "working tier" bucket -- one `put_object` to `S3_BILL_ARCHIVE_BUCKET` at
    `STANDARD_IA` instead of `DEEP_ARCHIVE` is both the archive and the readable copy at once.

    OPEN-235: takes bytes directly, not a path -- every test below constructs its own real
    content and its own real MD5 of it, matching how the caller (`archive_bill_versions`)
    actually computes `local_md5`, rather than asserting against a hand-typed hash string."""

    def _content_and_md5(self, content: bytes):
        return content, hashlib.md5(content).hexdigest()

    def test_successful_upload_writes_standard_ia_and_returns_uri(self):
        content, md5 = self._content_and_md5(b"pdf bytes")
        object_key = "bills/raw/fl/2026/lower/x.pdf"
        client = mock.Mock()
        client.head_object.return_value = {"ETag": f'"{md5}"'}
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, object_key, md5)

        assert result == f"s3://{S3_BILL_ARCHIVE_BUCKET}/{object_key}"
        assert client.put_object.call_count == 1  # exactly one write, not two
        put = client.put_object.call_args
        assert put.kwargs["Bucket"] == S3_BILL_ARCHIVE_BUCKET
        assert put.kwargs["Key"] == object_key
        assert put.kwargs["Body"] == content  # the exact bytes given, no local round trip
        # STANDARD_IA, not DEEP_ARCHIVE -- OPEN-238's whole point is that this single write is
        # immediately readable, no ~12h restore, in the same bucket the historical Deep-Archive
        # corpus already lives in.
        assert put.kwargs["StorageClass"] == "STANDARD_IA"
        # The verify call has to check the same object it just wrote, not some other one --
        # a regression that verified the wrong key/bucket would otherwise still pass.
        client.head_object.assert_called_once_with(
            Bucket=S3_BILL_ARCHIVE_BUCKET, Key=object_key
        )

    def test_head_object_failure_returns_none(self):
        # The write can succeed while the verification call itself fails (permissions,
        # throttling, a transient network blip) -- this must return None exactly like a
        # put_object failure does, not treat "the write didn't raise" as good enough.
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.head_object.side_effect = _client_error("AccessDenied")
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_head_object_non_client_botocore_error_returns_none(self):
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.head_object.side_effect = NoCredentialsError()
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_put_object_failure_returns_none(self):
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.put_object.side_effect = _client_error("AccessDenied")
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_etag_mismatch_returns_none(self):
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.head_object.return_value = {"ETag": '"not-the-real-md5"'}
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_multipart_etag_returns_none(self):
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.head_object.return_value = {"ETag": f'"{md5}-2"'}
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_client_construction_failure_returns_none(self):
        # _get_s3_client() itself can raise (e.g. ProfileNotFound from a misconfigured
        # AWS_PROFILE) before any put_object/head_object call is even reachable -- this must
        # be caught too, not just failures from calls made on an already-constructed client.
        content, md5 = self._content_and_md5(b"pdf bytes")
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client",
            side_effect=NoCredentialsError(),
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_non_client_botocore_error_returns_none(self):
        # NoCredentialsError/EndpointConnectionError are BotoCoreError subclasses, not
        # ClientError -- they never got a response from S3 to wrap at all. This function's
        # contract is "None on any failure," so these must be caught too, not just ClientError.
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.put_object.side_effect = NoCredentialsError()
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None

    def test_endpoint_connection_error_returns_none(self):
        content, md5 = self._content_and_md5(b"pdf bytes")
        client = mock.Mock()
        client.put_object.side_effect = EndpointConnectionError(
            endpoint_url="https://s3.amazonaws.com"
        )
        with mock.patch(
            "openstates.cli.text_extract._get_s3_client", return_value=client
        ):
            result = _upload_and_verify_direct(content, "some/key", md5)
        assert result is None


class TestUploadAndVerifyViaWrapper:
    """The original, Mac-only upload path.

    OPEN-235: this now writes the given bytes to a throwaway tempfile (the wrapper binary --
    a separate external CLI tool this repo doesn't control -- only accepts a local path as its
    upload source) rather than reading them back from the bill's permanent DDP-HOT path. These
    tests exercise the real subprocess/tempfile plumbing against a fake wrapper script (same
    stand-in-binary convention cloud_archiver.py's own tests use for OS_TEXT_EXTRACT), not a
    mocked-out function -- the specific thing OPEN-235 changed here is what file the wrapper
    gets handed and whether it survives the call, so that's what needs real coverage, not the
    already-covered ETag logic (see TestCheckEtag) or dispatch (see TestUploadAndVerifyDispatch).
    """

    def _fake_wrapper(self, tmp_path, *, put_exit=0, info_exit=0, etag=None,
                       record_dir=None):
        """A stand-in for S3_BILL_ARCHIVE_WRAPPER understanding `put <path> <key>` (copies the
        given path's content into record_dir/put_content so a test can inspect exactly what
        bytes it was handed) and `info <key>` (prints a JSON ETag payload)."""
        script = tmp_path / "fake_wrapper.sh"
        etag_json = json.dumps({"ETag": f'"{etag}"'}) if etag else "{}"
        lines = [
            "#!/usr/bin/env bash",
            'if [ "$1" = "put" ]; then',
            f'    cp "$2" {str(record_dir / "put_content")!r}',
            f'    echo -n "$3" > {str(record_dir / "put_key")!r}',
            f"    exit {put_exit}",
            'elif [ "$1" = "info" ]; then',
            f"    echo '{etag_json}'",
            f"    exit {info_exit}",
            "fi",
        ]
        script.write_text("\n".join(lines) + "\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    def test_successful_upload_hands_the_wrapper_exactly_the_given_bytes(
        self, tmp_path, monkeypatch
    ):
        record_dir = tmp_path / "record"
        record_dir.mkdir()
        content = b"the real document bytes, not read back from anywhere"
        md5 = hashlib.md5(content).hexdigest()
        monkeypatch.setattr(
            "openstates.cli.text_extract.S3_BILL_ARCHIVE_WRAPPER",
            self._fake_wrapper(tmp_path, etag=md5, record_dir=record_dir),
        )

        result = _upload_and_verify_via_wrapper(content, "bills/raw/fl/x.pdf", md5)

        assert result == f"s3://{S3_BILL_ARCHIVE_BUCKET}/bills/raw/fl/x.pdf"
        assert (record_dir / "put_content").read_bytes() == content
        assert (record_dir / "put_key").read_text() == "bills/raw/fl/x.pdf"

    def test_temp_file_does_not_survive_the_call(self, tmp_path, monkeypatch):
        # The whole point of OPEN-235: nothing this function touches on local disk should
        # outlive the call -- there is no more "the document is also sitting on DDP-HOT now."
        record_dir = tmp_path / "record"
        record_dir.mkdir()
        content = b"ephemeral"
        md5 = hashlib.md5(content).hexdigest()
        seen_path_file = tmp_path / "seen_temp_path.txt"
        script = tmp_path / "fake_wrapper_capture_path.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'if [ "$1" = "put" ]; then echo -n "$2" > {str(seen_path_file)!r}; exit 0; fi\n'
            f"echo '{{\"ETag\": \"\\\"{md5}\\\"\"}}'\n"
            "exit 0\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(
            "openstates.cli.text_extract.S3_BILL_ARCHIVE_WRAPPER", str(script)
        )

        _upload_and_verify_via_wrapper(content, "some/key", md5)

        temp_path = seen_path_file.read_text()
        assert not __import__("os").path.exists(temp_path)

    def test_put_failure_returns_none(self, tmp_path, monkeypatch):
        record_dir = tmp_path / "record"
        record_dir.mkdir()
        monkeypatch.setattr(
            "openstates.cli.text_extract.S3_BILL_ARCHIVE_WRAPPER",
            self._fake_wrapper(tmp_path, put_exit=1, record_dir=record_dir),
        )
        result = _upload_and_verify_via_wrapper(b"data", "some/key", "abc123")
        assert result is None

    def test_info_failure_returns_none(self, tmp_path, monkeypatch):
        record_dir = tmp_path / "record"
        record_dir.mkdir()
        monkeypatch.setattr(
            "openstates.cli.text_extract.S3_BILL_ARCHIVE_WRAPPER",
            self._fake_wrapper(tmp_path, info_exit=1, record_dir=record_dir),
        )
        result = _upload_and_verify_via_wrapper(b"data", "some/key", "abc123")
        assert result is None

    def test_etag_mismatch_returns_none(self, tmp_path, monkeypatch):
        record_dir = tmp_path / "record"
        record_dir.mkdir()
        monkeypatch.setattr(
            "openstates.cli.text_extract.S3_BILL_ARCHIVE_WRAPPER",
            self._fake_wrapper(tmp_path, etag="not-the-real-md5", record_dir=record_dir),
        )
        result = _upload_and_verify_via_wrapper(b"data", "some/key", "abc123")
        assert result is None

    def test_missing_wrapper_binary_returns_none(self):
        # OSError (e.g. the binary doesn't exist at all) must be caught the same as a
        # CalledProcessError -- this function's contract is "None on any failure."
        with mock.patch(
            "openstates.cli.text_extract.S3_BILL_ARCHIVE_WRAPPER",
            "/nonexistent/wrapper/binary/does-not-exist",
        ):
            result = _upload_and_verify_via_wrapper(b"data", "some/key", "abc123")
        assert result is None
