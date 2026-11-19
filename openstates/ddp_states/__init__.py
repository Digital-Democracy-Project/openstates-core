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
import pkgutil
import typing

# What `os-text-extract relabel-non-bills` calls an amendment paper (OPEN-517); kept here (OPEN-554) so a state's
# rules can name it without importing the generic code that reads them.
NON_BILL_AMENDMENT = "amendment"


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


def state_rule_for_name(jurisdiction_name: typing.Optional[str], name: str) -> typing.Optional[typing.Callable]:
    """`state_rule` for a state given by its name (`Jurisdiction.name`, e.g. "North Carolina"), as the text step and
    the version rules receive it (OPEN-554). None for a name that is not a state (a test jurisdiction, the federal
    one) or a state without that function."""
    from openstates.metadata import lookup

    if not jurisdiction_name:
        return None
    try:
        abbr = lookup(name=jurisdiction_name).abbr
    except KeyError:
        return None
    return state_rule(abbr, name)


def state_values(attr: str) -> typing.Dict[str, typing.Any]:
    """{state code: value} for every state module that defines `attr` (OPEN-554). The tables the generic code keeps
    (not-a-version notes, version order, readers) are built from these, so a state's rule lives only in its file and
    there is no list of states to keep. Modules whose name starts with "_" are shared helpers, not states."""
    values = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, attr):
            values[info.name] = getattr(module, attr)
    return values


def state_values_by_name(attr: str) -> typing.Dict[str, typing.Any]:
    """`state_values` keyed by the state's name (`Jurisdiction.name`, e.g. "North Carolina"), the key the version
    rules use."""
    from openstates.metadata import lookup

    return {lookup(abbr=code).name: value for code, value in state_values(attr).items()}
