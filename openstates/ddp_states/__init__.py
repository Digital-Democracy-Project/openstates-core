"""
DDP's per-state normalizing rules, one module per state (`openstates/ddp_states/<state>.py`, lower-case postal code;
PLAN-state-specific-handling §4.2). A DDP-owned directory upstream does not have, so upstream merges never touch it.

A state module defines only the functions it needs; a missing one means "standard data, nothing to do". Each is meant
to run in exactly two places: the state's scraper on what it parsed, before it emits a bill, and `os-text-extract
relabel` on the stored history of bills the scraper will not re-scrape (§4.4, §6 Phase 3). **No scraper calls this
package yet** (2026-11-17): wiring the state's scraper is an acceptance line of each state's ticket (OPEN-545, 547 to
552), and a state relabelled before its scraper is wired gets the old labels back on its next re-scrape. The labels two
functions answer (OPEN-546):

* ``classify_action(*, description, classification, chamber, date) -> list[str]``: an action's labels, given its
  text, the labels the scraper's categorizer gave it, its chamber (the organization's classification: ``upper``,
  ``lower``, ``legislature``, ``executive``) and its date.
* ``classify_bill(*, identifier, title, classification) -> list[str]``: a bill's labels, given the same.

Both return the whole label list, and must give the same answer when called again on their own answer (the re-label
pass refuses to write otherwise), so a second run, or a re-scrape of a re-labelled bill, changes nothing.
"""

import importlib
import typing


def state_rule(state: str, name: str) -> typing.Optional[typing.Callable]:
    """The function `name` of the state's module, or None when the state has no module or the module has no such
    function."""
    module_name = f"{__name__}.{state.lower()}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name != module_name:  # the module exists but one of its own imports is missing: say so
            raise
        return None
    return getattr(module, name, None)
