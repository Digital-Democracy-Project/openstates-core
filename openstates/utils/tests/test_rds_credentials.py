"""Tests for rds_credentials.py and its RESOLVE_RDS_LIVE opt-in in django.py (OPEN-260)."""

import json

import pytest

from openstates.utils.django import _resolve_database_url
from openstates.utils.rds_credentials import resolve_rds_database_url


class FakeSecretsManagerClient:
    def __init__(self, secret_string=None, error=None):
        self._secret_string = secret_string
        self._error = error
        self.get_secret_value_calls = []

    def get_secret_value(self, **kwargs):
        self.get_secret_value_calls.append(kwargs)
        if self._error:
            raise self._error
        return {"SecretString": self._secret_string}


def _real_shaped_secret(**overrides):
    secret = {
        "username": "openstates_admin",
        "password": "correct horse battery staple",
        "host": "ddp-openstates.cvxdhm1ogxug.us-east-1.rds.amazonaws.com",
        "port": 5432,
        "dbname": "openstates",
    }
    secret.update(overrides)
    return json.dumps(secret)


# ── resolve_rds_database_url ────────────────────────────────────────────────────────────────


def test_missing_secret_arn_refuses_without_calling_secrets_manager(monkeypatch):
    monkeypatch.delenv("RDS_CREDENTIALS_SECRET_ARN", raising=False)
    url, error = resolve_rds_database_url(secretsmanager_client=FakeSecretsManagerClient())

    assert url is None
    assert "RDS_CREDENTIALS_SECRET_ARN not set" in error


def test_successful_fetch_assembles_a_valid_postgres_url(monkeypatch):
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:1:secret:rds!x")
    client = FakeSecretsManagerClient(secret_string=_real_shaped_secret())

    url, error = resolve_rds_database_url(secretsmanager_client=client)

    assert error == ""
    assert url == (
        "postgresql://openstates_admin:correct%20horse%20battery%20staple"
        "@ddp-openstates.cvxdhm1ogxug.us-east-1.rds.amazonaws.com:5432/openstates"
    )


def test_password_with_url_special_characters_is_percent_encoded(monkeypatch):
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")
    client = FakeSecretsManagerClient(secret_string=_real_shaped_secret(password="p@ss:w/rd%25"))

    url, error = resolve_rds_database_url(secretsmanager_client=client)

    assert error == ""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    assert unquote(parsed.password) == "p@ss:w/rd%25"


def test_secrets_manager_api_error_fails_loudly_not_silently(monkeypatch):
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")
    client = FakeSecretsManagerClient(error=RuntimeError("AccessDeniedException"))

    url, error = resolve_rds_database_url(secretsmanager_client=client)

    assert url is None
    assert "AccessDeniedException" in error


def test_malformed_secret_shape_fails_cleanly_instead_of_raising(monkeypatch):
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")
    client = FakeSecretsManagerClient(secret_string=json.dumps({"username": "x"}))

    url, error = resolve_rds_database_url(secretsmanager_client=client)

    assert url is None
    assert "unexpected shape" in error


def test_boto3_client_construction_failure_fails_cleanly_instead_of_raising(monkeypatch):
    """pm-review: boto3.client() itself can raise, not just get_secret_value() -- both must
    land in the same (None, error) tuple contract."""
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")

    from unittest.mock import patch

    with patch("boto3.client", side_effect=RuntimeError("no region configured")):
        url, error = resolve_rds_database_url()

    assert url is None
    assert "no region configured" in error


def test_dbname_with_url_special_characters_is_percent_encoded(monkeypatch):
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")
    client = FakeSecretsManagerClient(secret_string=_real_shaped_secret(dbname="weird/db?name"))

    url, error = resolve_rds_database_url(secretsmanager_client=client)

    assert error == ""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    assert unquote(parsed.path.lstrip("/")) == "weird/db?name"


def test_null_field_in_secret_fails_cleanly_instead_of_producing_a_garbage_dsn(monkeypatch):
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")
    client = FakeSecretsManagerClient(secret_string=_real_shaped_secret(password=None))

    url, error = resolve_rds_database_url(secretsmanager_client=client)

    assert url is None
    assert "unexpected shape" in error


# ── _resolve_database_url (django.py's RESOLVE_RDS_LIVE opt-in) ────────────────────────────


def test_resolve_rds_live_false_does_not_enable_live_resolution(monkeypatch):
    """pm-review: a bare truthiness check on os.environ.get(...) would treat "false" (any
    non-empty string) as enabled -- a real operator footgun for anyone following the common
    RESOLVE_RDS_LIVE=false convention to mean "disabled"."""
    monkeypatch.setenv("RESOLVE_RDS_LIVE", "false")
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/openstates")

    assert _resolve_database_url() == "postgresql://local/openstates"


def test_resolve_rds_live_zero_does_not_enable_live_resolution(monkeypatch):
    monkeypatch.setenv("RESOLVE_RDS_LIVE", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/openstates")

    assert _resolve_database_url() == "postgresql://local/openstates"


def test_resolve_rds_live_unset_falls_back_to_database_url_env_var(monkeypatch):
    monkeypatch.delenv("RESOLVE_RDS_LIVE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/openstates")

    assert _resolve_database_url() == "postgresql://local/openstates"


def test_resolve_rds_live_unset_and_no_database_url_uses_documented_default(monkeypatch):
    monkeypatch.delenv("RESOLVE_RDS_LIVE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert _resolve_database_url() == "postgis://openstates:openstates@localhost/openstates"


def test_resolve_rds_live_set_ignores_database_url_and_resolves_live(monkeypatch):
    """The whole point: a stale DATABASE_URL in the environment must not win once an operator
    has explicitly opted into live resolution."""
    monkeypatch.setenv("RESOLVE_RDS_LIVE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stale-cached-value/openstates")
    monkeypatch.setenv("RDS_CREDENTIALS_SECRET_ARN", "arn:secret")

    from unittest.mock import patch

    with patch(
        "openstates.utils.django.resolve_rds_database_url",
        return_value=("postgresql://freshly-resolved/openstates", ""),
    ):
        assert _resolve_database_url() == "postgresql://freshly-resolved/openstates"


def test_resolve_rds_live_set_but_unresolvable_raises_loudly(monkeypatch):
    """Deliberately raises rather than silently falling back to the stale DATABASE_URL --
    an operator who opted into live resolution and can't get it should see that clearly, not
    have their command quietly proceed against whatever was cached."""
    monkeypatch.setenv("RESOLVE_RDS_LIVE", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://stale-cached-value/openstates")
    monkeypatch.delenv("RDS_CREDENTIALS_SECRET_ARN", raising=False)

    with pytest.raises(RuntimeError, match="RESOLVE_RDS_LIVE set but could not resolve"):
        _resolve_database_url()
