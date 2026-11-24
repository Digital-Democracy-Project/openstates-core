import collections
import json
import typing
from .base import BaseImporter
from ._types import _JsonDict, _DBSpec, _RelatedModels
from ..utils import get_pseudo_id, _make_pseudo_id
from ..exceptions import InvalidVoteEventError
from ..data.models import VoteEvent, VoteCount, PersonVote, VoteSource, BillAction
from .organizations import OrganizationImporter
from .bills import BillImporter


class VoteEventImporter(BaseImporter):
    _type = "vote_event"
    model_class = VoteEvent
    related_models: _RelatedModels = {
        "counts": (VoteCount, "vote_event_id", {}),
        "votes": (PersonVote, "vote_event_id", {}),
        "sources": (VoteSource, "vote_event_id", {}),
    }

    def __init__(self, jurisdiction_id: str, bill_importer: BillImporter, do_postimport=True):
        super(VoteEventImporter, self).__init__(jurisdiction_id, do_postimport)
        self.org_importer = OrganizationImporter(jurisdiction_id)
        self.bill_importer = bill_importer
        self.seen_bill_ids: typing.Set[str] = set()
        self.seen_action_ids: typing.Set[str] = set()
        self.vote_events_to_delete: typing.Set[str] = set()

    def get_object(self, vote_event: _JsonDict) -> VoteEvent:
        spec = {"legislative_session_id": vote_event["legislative_session_id"]}

        if not vote_event["identifier"] and not vote_event["bill_id"]:
            raise InvalidVoteEventError(
                'attempt to save a VoteEvent without an "identifier" or "bill_id"'
            )

        if vote_event["bill_id"]:
            if vote_event["bill_id"] not in self.seen_bill_ids:
                self.seen_bill_ids.add(vote_event["bill_id"])
                # keep a list of all the vote event ids that should be deleted
                self.vote_events_to_delete.update(
                    self.model_class.objects.filter(
                        bill_id=vote_event["bill_id"]
                    ).values_list("id", flat=True)
                )
            spec["bill_id"] = vote_event["bill_id"]

        if vote_event.get("dedupe_key"):
            # replace entire spec with dedupe key
            spec = {"dedupe_key": vote_event.get("dedupe_key")}
        elif vote_event["identifier"]:
            # if there's an identifier, just use it and the bill_id and the session
            spec["identifier"] = vote_event["identifier"]
        else:
            # otherwise use the motion, start_date, and org as well
            spec.update(
                {
                    "motion_text": vote_event["motion_text"],
                    "start_date": vote_event["start_date"],
                    "organization_id": vote_event["organization_id"],
                }
            )

        return self.model_class.objects.prefetch_related("votes__voter").get(**spec)

    def limit_spec(self, spec: _JsonDict) -> _DBSpec:
        spec["legislative_session__jurisdiction_id"] = self.jurisdiction_id
        return spec

    def prepare_for_db(self, data: _JsonDict) -> _JsonDict:
        session = self.get_session(data.pop("legislative_session"))
        data["legislative_session_id"] = session.id
        organization_classification = data.pop("organization")
        data["organization_id"] = self.org_importer.resolve_json_id(
            organization_classification
        )

        bill = data.pop("bill")
        if bill and bill.startswith("~"):
            # unpack psuedo id and apply filter in case there are any that alter it
            bill = get_pseudo_id(bill)
            self.bill_importer.apply_transformers(bill)
            bill = _make_pseudo_id(**bill)

        data["bill_id"] = self.bill_importer.resolve_json_id(bill)
        bill_action = data.pop("bill_action")
        if bill_action:
            try:
                action = BillAction.objects.get(
                    bill_id=data["bill_id"],
                    description=bill_action,
                    date=data["start_date"],
                    organization_id=data["organization_id"],
                )
                # seen_action_ids is for ones being added in this import
                # action.vote is already set if action was set on prior import
                if action.id in self.seen_action_ids or hasattr(action, "vote"):
                    self.warning(
                        "can not match two VoteEvents to %s: %s", action.id, bill_action
                    )
                else:
                    data["bill_action_id"] = action.id
                    self.seen_action_ids.add(action.id)
            except BillAction.DoesNotExist:
                self.warning(
                    "could not match VoteEvent to %s %s %s",
                    bill,
                    bill_action,
                    data["start_date"],
                )
            except BillAction.MultipleObjectsReturned as e:
                self.warning(
                    "could not match VoteEvent to %s %s %s: %s",
                    bill,
                    bill_action,
                    data["start_date"],
                    e,
                )

        self.resolve_voters(
            data["votes"], session.start_date, session.end_date,
            json.loads(organization_classification[1:]).get("classification", None),
            data["start_date"],
        )
        return data

    def resolve_voters(
        self,
        votes: typing.List[_JsonDict],
        start_date: typing.Optional[str],
        end_date: typing.Optional[str],
        org_classification: typing.Optional[str],
        date: typing.Optional[str],
    ) -> None:
        """Replace each vote's pseudo voter id with a person id (or None) in place."""
        pseudo_ids = [vote["voter_id"] for vote in votes]
        for vote, pseudo_id in zip(votes, pseudo_ids):
            # OPEN-767: the vote's own date settles a name two members share in the session
            vote["voter_id"] = self.resolve_person(
                pseudo_id, start_date, end_date, org_classification, date=date
            )

        # OPEN-844: a name several seated members fit (a bare surname, with three members of that
        # surname seated) is settled by the roll call itself when the others are already matched
        # on it under their own names: the one candidate left is matched. Two or more left, or the
        # same person left for two names, stays unmatched. A matched vote is never changed.
        matched = {vote["voter_id"] for vote in votes if vote["voter_id"]}
        left_for: typing.Dict[int, str] = {}
        for n, (vote, pseudo_id) in enumerate(zip(votes, pseudo_ids)):
            if vote["voter_id"] is None:
                left = self.ambiguous_person_candidates(
                    pseudo_id, start_date, end_date, org_classification, date
                ) - matched
                if len(left) == 1:
                    left_for[n] = left.pop()
        times_left = collections.Counter(left_for.values())
        for n, person_id in left_for.items():
            if times_left[person_id] == 1:
                votes[n]["voter_id"] = person_id
                self.info("%s matched on %s by the other voters of its roll call", pseudo_ids[n], date)

    def postimport(self) -> None:
        # be sure not to delete vote events that were imported (meaning updated) this time through
        self.vote_events_to_delete.difference_update(self.json_to_db_id.values())
        # everything remaining, goodbye
        self.model_class.objects.filter(id__in=self.vote_events_to_delete).delete()
