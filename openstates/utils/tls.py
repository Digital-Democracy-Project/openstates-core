"""Certificate checking for every legislature request (OPEN-800).

Ramon's decision, 2026-11-23: "Check by default, named exceptions". Every HTTPS request made
through `VerifyingScraper` (the base of every openstates scraper, and the archive's text fetch in
`cli/text_extract.py`) checks the site's certificate. The only way to skip the check is a named,
dated entry in `TLS_VERIFY_EXCEPTIONS` below, and it applies to that one host only. There is no
global switch: the old `VERIFY_CERTS` environment variable and `os-update --no-verify` flag are
gone, and a scraper passing `verify=False` (or setting `self.verify = False`) no longer turns the
check off.

To add an exception, Ramon approves it from a measurement (OPEN-800's per-host table is the
first one); the entry says why and which ticket.
"""
import typing
from urllib.parse import urlsplit

import scrapelib


class TlsException(typing.NamedTuple):
    reason: str  # what fails today, as measured (e.g. "hostname mismatch: cert is for X")
    date: str  # YYYY-MM-DD the exception was approved
    ticket: str  # the Jira ticket that measured and approved it


# Host (lower case, exact match, no port) -> why its certificate is not checked.
# Empty until Ramon approves an entry; OPEN-800's PR lists the proposed ones.
TLS_VERIFY_EXCEPTIONS: typing.Dict[str, TlsException] = {}


def verify_for(url: str, requested: typing.Union[None, bool, str] = None) -> typing.Union[bool, str]:
    """The `verify` value a request to `url` must use.

    A named exception for the URL's host turns the check off for that host. Otherwise the check
    is on: a caller's CA-bundle path (a str, which still verifies) is kept, and anything else,
    including a caller's `verify=False`, becomes True.
    """
    host = (urlsplit(url).hostname or "").lower()
    if host in TLS_VERIFY_EXCEPTIONS:
        return False
    if isinstance(requested, str):
        return requested
    return True


class VerifyingScraper(scrapelib.Scraper):
    """A scrapelib.Scraper whose every request (redirects included) follows `verify_for`.

    `send` is where requests hands over each prepared request, the first one and each redirect
    hop (Session.resolve_redirects calls send again), so deciding here means a redirect to
    another host is judged by that host, not by the one the request started at.
    """

    def send(self, request, **kwargs):  # type: ignore
        kwargs["verify"] = verify_for(request.url, kwargs.get("verify"))
        return super().send(request, **kwargs)
