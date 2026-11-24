import datetime
from typing import Union
from .base import BaseImporter, _is_full_date
from ._types import _JsonDict, Model
from ..data.models import (
    Organization,
    Bill,
    RelatedBill,
    BillAbstract,
    BillTitle,
    BillIdentifier,
    BillAction,
    BillActionRelatedEntity,
    BillSponsorship,
    BillSource,
    BillDocument,
    BillVersion,
    BillDocumentLink,
    BillVersionLink,
)
from .computed_fields import update_bill_fields
from .organizations import OrganizationImporter
from ..utils.version_ordering import ACTIONS_REVERSED_KEY, ORIGINAL_CLASSIFICATION_KEY
from ..ddp_states import state_rule
from ..utils import jid_to_abbr


def resolve_related_bills(jurisdiction_id: str, session: Union[str, None], logger) -> None:
    # go through all RelatedBill objs that are attached to a bill in this jurisdiction and
    # are currently unresolved
    if session is not None:
        session_log = f"-{session}"
        related_bills = RelatedBill.objects.filter(
            bill__legislative_session__jurisdiction_id=jurisdiction_id,
            bill__legislative_session__identifier=session,
            related_bill=None,
        )
    else:
        session_log = ""
        related_bills = RelatedBill.objects.filter(
            bill__legislative_session__jurisdiction_id=jurisdiction_id,
            related_bill=None,
        )
    logger.info(f"Found {len(related_bills)} unresolved bill relationships in {jurisdiction_id}{session_log}")

    # go session-by-session and see if we can find matching candidates
    # we do this to reduce the number of SELECT queries we run in cases where there are many relations unresolved
    sessions = {}
    for rb in related_bills:
        if rb.legislative_session not in sessions:
            sessions[rb.legislative_session] = [rb.identifier]
        else:
            sessions[rb.legislative_session].append(rb.identifier)

    session_candidate_bills = {}
    for session in dict.keys(sessions):
        candidates = list(
            Bill.objects.filter(
                identifier__in=sessions[session],
                legislative_session__identifier=session,
                legislative_session__jurisdiction_id=jurisdiction_id,
            )
        )
        session_candidate_bills[session] = {}
        for bill in candidates:
            session_candidate_bills[session][bill.identifier] = bill

    matches_found = 0
    for rb in related_bills:
        if rb.identifier in session_candidate_bills[rb.legislative_session]:
            rb.related_bill = session_candidate_bills[rb.legislative_session][rb.identifier]
            rb.save()
            matches_found += 1
            logger.debug(f"Resolved {rb.legislative_session} {rb.bill.identifier}")
        else:
            logger.debug(f"FAILED to resolve {rb.legislative_session} {rb.bill.identifier}")

    logger.info(f"Resolved {matches_found} bills out of {len(related_bills)} bills needing resolution")


def _is_real_day(day: str) -> bool:
    try:
        datetime.date.fromisoformat(day)
        return True
    except ValueError:
        return False


def carried_over_since(actions: list, session_start: str) -> str:
    """OPEN-809: the day a bill was introduced (its first dated action) when that is before its session began,
    so the bill was carried over from an earlier session; "" otherwise, or when either date is unknown."""
    days = [a["date"][:10] for a in actions if _is_full_date(a.get("date")) and _is_real_day(a["date"][:10])]
    introduced = min(days) if days else ""
    return introduced if _is_full_date(session_start) and introduced and introduced < session_start[:10] else ""


class BillImporter(BaseImporter):
    _type = "bill"
    model_class = Bill
    related_models = {
        "abstracts": (BillAbstract, "bill_id", {}),
        "other_titles": (BillTitle, "bill_id", {}),
        "other_identifiers": (BillIdentifier, "bill_id", {}),
        "actions": (
            BillAction,
            "bill_id",
            {"related_entities": (BillActionRelatedEntity, "action_id", {})},
        ),
        "related_bills": (RelatedBill, "bill_id", {}),
        "sponsorships": (BillSponsorship, "bill_id", {}),
        "sources": (BillSource, "bill_id", {}),
        "documents": (
            BillDocument,
            "bill_id",
            {"links": (BillDocumentLink, "document_id", {})},
        ),
        "versions": (
            BillVersion,
            "bill_id",
            {"links": (BillVersionLink, "version_id", {})},
        ),
    }
    preserve_order = {"actions"}
    keep_extras_keys = (ORIGINAL_CLASSIFICATION_KEY, ACTIONS_REVERSED_KEY)

    def __init__(self, jurisdiction_id: str, do_postimport=True):
        super(BillImporter, self).__init__(jurisdiction_id, do_postimport)
        self.org_importer = OrganizationImporter(jurisdiction_id)
        # OPEN-550: the state's own action-label rule (openstates/ddp_states/<state>.py), applied to every imported
        # action so a re-scrape gives the labels `os-text-extract relabel` wrote. None (nothing to do) for a state
        # without one, or a jurisdiction that is not a state in the metadata.
        try:
            state = jid_to_abbr(jurisdiction_id)
        except KeyError:
            state = None
        self.classify_action = state_rule(state, "classify_action") if state else None
        self.org_classification_cache: dict = {}

    def get_object(self, bill: _JsonDict) -> Model:
        spec = {
            "legislative_session_id": bill["legislative_session_id"],
            "identifier": bill["identifier"],
        }
        # bills should be unique per session + identifier, no need to use organization
        #   (note: this used to be different, but allowed duplicates)

        return self.model_class.objects.prefetch_related(
            "actions__related_entities", "versions__links", "documents__links"
        ).get(**spec)

    def limit_spec(self, spec: _JsonDict) -> _JsonDict:
        spec["legislative_session__jurisdiction_id"] = self.jurisdiction_id
        return spec

    def prepare_for_db(self, data: _JsonDict) -> _JsonDict:
        session = self.get_session(data.pop("legislative_session"))
        data["legislative_session_id"] = session.id

        if data["from_organization"]:
            data["from_organization_id"] = self.org_importer.resolve_json_id(
                data.pop("from_organization")
            )

        for action in data["actions"]:
            action["organization_id"] = self.org_importer.resolve_json_id(
                action["organization_id"]
            )
            if self.classify_action:
                action["classification"] = self.apply_state_action_rule(action)
            for entity in action["related_entities"]:
                if "organization_id" in entity:
                    entity["organization_id"] = self.org_importer.resolve_json_id(
                        entity["organization_id"]
                    )
                elif "person_id" in entity:
                    entity["person_id"] = self.resolve_person(
                        entity["person_id"], session.start_date, session.end_date
                    )

        introduced = carried_over_since(data["actions"], session.start_date)
        for sponsor in data["sponsorships"]:
            if sponsor.get("person_id"):
                pseudo_id = sponsor["person_id"]
                sponsor["person_id"] = self.resolve_person(
                    pseudo_id,
                    session.start_date,
                    session.end_date,
                )
                # OPEN-809: a bill carried over from an earlier session keeps sponsors who left before this
                # session began (Hawaii 2026: two members who left in 2025, on January 2025 bills). When this
                # session's members give nobody (not several people), the members seated on the day the bill
                # was introduced decide, through the same lookup with that day as the window (its bounds are
                # strict, so the day before to the day after: a seat starting or ending on the day counts, as in
                # OPEN-767) and, for a tie, OPEN-767's match on the day. A sponsor this session already matches
                # is never re-decided.
                if (
                    sponsor["person_id"] is None
                    and introduced
                    # the session lookup's key (resolve_person's): no chamber, no OPEN-817 vote day
                    and (pseudo_id, session.start_date, session.end_date, None, None)
                    not in self.ambiguous_people
                ):
                    day = datetime.date.fromisoformat(introduced)
                    sponsor["person_id"] = self.resolve_person(
                        pseudo_id,
                        str(day - datetime.timedelta(days=1)),
                        str(day + datetime.timedelta(days=1)),
                        date=introduced,
                    )
            if sponsor.get("organization_id"):
                sponsor["organization_id"] = self.org_importer.resolve_json_id(
                    sponsor["organization_id"], allow_no_match=True
                )

        return data

    def apply_state_action_rule(self, action: _JsonDict) -> list:
        """
        OPEN-550: the action's labels as the state's `classify_action` gives them, read exactly as `os-text-extract
        relabel` reads a stored action (`_relabel_plan`): its text, its labels, its organization's classification as
        the chamber and its date string, duplicates dropped. So an import and a relabel of the same line agree, and a
        state relabelled once keeps its new labels when it is re-scraped.
        """
        org_id = action["organization_id"]
        if org_id not in self.org_classification_cache:
            self.org_classification_cache[org_id] = (
                Organization.objects.filter(id=org_id)
                .values_list("classification", flat=True)
                .first()
            )
        return list(
            dict.fromkeys(
                self.classify_action(
                    description=action["description"],
                    classification=list(action["classification"]),
                    chamber=self.org_classification_cache[org_id],
                    date=action["date"],
                )
            )
        )

    def postimport(self) -> None:
        resolve_related_bills(self.jurisdiction_id, None, self.logger)

    def update_computed_fields(self, obj: Model) -> None:
        update_bill_fields(obj, save=False)
