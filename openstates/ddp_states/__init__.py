"""
DDP's per-state normalizing rules, one module per state (`openstates/ddp_states/<state>.py`, lower-case postal code;
PLAN-state-specific-handling §4.2). A DDP-owned directory upstream does not have, so upstream merges never touch it.

A state module defines only the functions it needs; a missing one means "standard data, nothing to do". Each runs in
exactly two places: core's bill importer on every action it imports (`BillImporter.prepare_for_db`, OPEN-550), and
`os-text-extract relabel` on the stored history (§4.4, §6 Phase 3). Both read an action the same way (its text, its
labels, its organization's classification as the chamber, its date string), so a relabelled state's re-scrape keeps
the new labels instead of bringing the old ones back. The importer applies `classify_action` only; `classify_bill` is
applied by `relabel` alone so far. The labels two functions answer (OPEN-546):

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
