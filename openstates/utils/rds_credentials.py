"""Resolves the live RDS connection string from Secrets Manager at call time.

OPEN-260: this is the openstates-core-side twin of ddp-sync's `services/rds_credentials.py`
(same repro, same fix, two independent Python packages -- there's no shared dependency between
them to put one copy in). Both exist because RDS's own "manage master credentials in Secrets
Manager" feature rotates the master password automatically every 7 days, and this codebase used
to read a cached `DATABASE_URL` that only got refreshed when someone happened to re-render it --
which is exactly what broke the `os-text-extract` RDS backfill/dry-run tooling and `quality_check.
py` on 2026-09-09, the same afternoon a rotation fired (see ddp-sync's OPEN-192 ops-handoff
thread for the full incident).

This module is opt-in, not the default: `init_django()` and `quality_check.py` still read
`DATABASE_URL` from the environment as before for the common case (a human pointing either tool
at their own local Postgres, or a Fargate container whose `DATABASE_URL` override was already
resolved live moments earlier by ddp-sync's own launch code). Setting `RESOLVE_RDS_LIVE=1` tells
either tool to resolve this module's DSN instead and use it in place of whatever `DATABASE_URL`
was set to -- for the specific case this ticket exists for: a human running an ad-hoc RDS
backfill or dry-run command who wants the current credential without first having to know
whether someone else's cached `.env` is stale.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote


def resolve_rds_database_url(secretsmanager_client=None) -> tuple[str | None, str]:
    """Fetch the current RDS credential from Secrets Manager and build a DSN.

    Returns (url, error): on success `error` is "" and `url` is a ready-to-use
    `postgresql://...` string; on any failure `url` is None and `error` describes what went
    wrong. Never falls back to a cached/stale value -- a failure here should be loud and
    visible to the caller, not silently absorbed into "proceed anyway."

    `secretsmanager_client` is injectable for tests; real callers should leave it unset.
    """
    secret_arn = os.environ.get("RDS_CREDENTIALS_SECRET_ARN")
    if not secret_arn:
        return None, "RDS_CREDENTIALS_SECRET_ARN not set -- refusing to guess which secret to read"

    if secretsmanager_client is None:
        import boto3

        region = os.environ.get("AWS_REGION", "us-east-1")
        secretsmanager_client = boto3.client("secretsmanager", region_name=region)

    try:
        response = secretsmanager_client.get_secret_value(SecretId=secret_arn)
    except Exception as e:  # noqa: BLE001 -- any boto3/network failure is equally "can't proceed"
        return None, f"could not fetch RDS credential from Secrets Manager: {e}"

    try:
        secret = json.loads(response["SecretString"])
        username = secret["username"]
        password = secret["password"]
        host = secret["host"]
        port = secret["port"]
        dbname = secret["dbname"]
    except (KeyError, ValueError, TypeError) as e:
        return None, f"RDS credential secret has an unexpected shape: {e}"

    url = (
        f"postgresql://{quote(str(username), safe='')}:{quote(str(password), safe='')}"
        f"@{host}:{port}/{dbname}"
    )
    return url, ""
