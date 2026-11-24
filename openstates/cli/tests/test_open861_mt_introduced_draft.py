"""OPEN-861: Montana's core rules name the relation by which an introduced bill links to its draft's record.

api-v3 (`api/bill_types.py`) reads `INTRODUCED_DRAFT_RELATION` through `state_values` and leaves a record that a bill of
the same jurisdiction links to with that type out of `bill_types_only` lists, search and counts. The value must be the
relation type the Montana scraper writes (scrapers/mt/bills.py, `add_related_bill(..., "replaces")`), and no other
state may name one by accident: Louisiana and Massachusetts use "replaces" between real bills.
"""

from openstates.ddp_states import state_values
from openstates.scrape.bill import Bill


def test_only_montana_names_an_introduced_draft_relation():
    assert state_values("INTRODUCED_DRAFT_RELATION") == {"mt": "replaces"}


def test_the_relation_is_one_a_scraped_bill_can_carry():
    bill = Bill("SB 1", legislative_session="2027", chamber="upper", title="A bill")
    bill.add_source("https://bills.legmt.gov/")
    bill.add_related_bill("LC 1", "2027", state_values("INTRODUCED_DRAFT_RELATION")["mt"])
    bill.validate()
    assert bill.related_bills[0]["relation_type"] == "replaces"
