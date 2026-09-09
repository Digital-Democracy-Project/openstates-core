import os
import django  # type: ignore
from django import conf  # type: ignore
import dj_database_url  # type: ignore

from openstates.utils.rds_credentials import resolve_rds_database_url


def _resolve_database_url() -> str:
    """Returns the DATABASE_URL init_django() should use.

    OPEN-260: RESOLVE_RDS_LIVE=1 opts into resolving the current RDS credential from Secrets
    Manager instead of trusting whatever DATABASE_URL happens to be set in this process's
    environment -- for a human running an ad-hoc RDS backfill/dry-run command who wants the
    current credential without first having to know whether a cached .env is stale (RDS's own
    automatic 7-day rotation broke exactly this on 2026-09-09). Off by default: the common case
    -- a local Postgres DATABASE_URL, or a Fargate container whose override was already
    resolved live moments earlier by ddp-sync's own launch code -- is unaffected.
    """
    if os.environ.get("RESOLVE_RDS_LIVE"):
        url, error = resolve_rds_database_url()
        if error:
            raise RuntimeError(f"RESOLVE_RDS_LIVE set but could not resolve an RDS credential: {error}")
        return url

    return os.environ.get("DATABASE_URL", "postgis://openstates:openstates@localhost/openstates")


def init_django() -> None:  # pragma: no cover
    DATABASE_URL = _resolve_database_url()
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
    application_name = "os_core"
    if "OPTIONS" not in DATABASES:
        DATABASES["default"]["OPTIONS"] = {"application_name": application_name}
    else:
        DATABASES["default"]["OPTIONS"]["application_name"] = application_name

    try:
        conf.settings.configure(
            conf.global_settings,
            SECRET_KEY="not-important",
            DEBUG=False,
            INSTALLED_APPS=(
                "django.contrib.contenttypes",
                "openstates.data",
            ),
            DATABASES=DATABASES,
            TIME_ZONE="UTC",
            MIDDLEWARE_CLASSES=(),
        )
        django.setup()
    except RuntimeError as e:
        if "Settings already configured." not in str(e):
            raise RuntimeError(f"Encountered error {e}")
