"""One per-state value from ddp-open-states' jurisdiction manifest, `jurisdictions.yaml` (OPEN-555).

PLAN-state-specific-handling §4.2 point 2: per-state values (fetch limits, MA's Speaker by session, NC's bare-key
session, ...) go to the manifest, not into code. Until both hosts and the scrapers image run a manifest that carries a
value (§6 Phase 4 step 3), every reader keeps its old constant as the fallback:

    manifest_value("fl", "waf.requests_per_minute", 10)

returns the manifest's value, or the fallback when the file, the state or the field is missing, the value is null, or
it is not of the fallback's type (a manifest that says `"ten"` never reaches a scraper). Never raises.

The file is `$DDP_JURISDICTIONS_MANIFEST` (the scrapers image sets it to /app/jurisdictions.yaml), else
`./jurisdictions.yaml`. The manifest's own validator (ddp-open-states validate_jurisdictions.py) checks each field's
shape; this only reads.
"""
import logging
import os
import typing

import yaml

logger = logging.getLogger("openstates")

_cache: typing.Dict[str, typing.Tuple[float, typing.Optional[dict]]] = {}


def manifest_path() -> str:
    return os.environ.get("DDP_JURISDICTIONS_MANIFEST") or "jurisdictions.yaml"


def _load(path: str) -> typing.Optional[dict]:
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("OPEN-555: jurisdiction manifest %s unreadable (%s); using built-in values", path, exc)
        data = None
    data = data if isinstance(data, dict) else None
    _cache[path] = (mtime, data)
    return data


def manifest_value(state: str, field: str, fallback, path: typing.Optional[str] = None):
    """`state`'s dotted `field` (e.g. "waf.requests_per_minute") from the manifest, else `fallback` (see module doc)."""
    value: typing.Any = (_load(path or manifest_path()) or {}).get(str(state).lower())
    for part in field.split("."):
        if not isinstance(value, dict):
            return fallback
        value = value.get(part)
    if value is None:
        return fallback
    if fallback is not None and (
        not isinstance(value, type(fallback)) or isinstance(value, bool) != isinstance(fallback, bool)
    ):
        logger.warning("OPEN-555: manifest %s.%s is %r, not a %s; using %r", state, field, value,
                       type(fallback).__name__, fallback)
        return fallback
    return value
