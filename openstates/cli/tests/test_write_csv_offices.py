import csv
from openstates.cli.people import write_csv

PERSON = """\
id: ocd-person/11111111-1111-1111-1111-111111111111
name: Test Person
party:
- name: Independent
roles:
- type: lower
  district: "1"
  jurisdiction: ocd-jurisdiction/country:us/state:fl/government
  start_date: "2020-01-01"
offices:
{offices}
"""


def _row(tmp_path, offices):
    f = tmp_path / "p.yml"
    f.write_text(PERSON.format(offices=offices))
    out = tmp_path / "out.csv"
    write_csv([f], "ocd-jurisdiction/country:us/state:fl/government", str(out))
    return next(csv.DictReader(open(out)))


def test_capitol_office_is_written(tmp_path):
    # before the upstream fix the capitol columns were always empty
    row = _row(tmp_path, "- classification: capitol\n  address: 1 Main St\n  voice: 850-555-0100\n  fax: 850-555-0101")
    assert (row["capitol_address"], row["capitol_voice"], row["capitol_fax"]) == ("1 Main St", "850-555-0100", "850-555-0101")


def test_mail_office_wins_address_but_not_phone(tmp_path):
    offices = (
        "- classification: district\n  address: 2 Street Address\n  voice: 850-555-0200\n  fax: 850-555-0201\n"
        "- classification: district-mail\n  address: PO Box 3\n  voice: 850-555-0999"
    )
    row = _row(tmp_path, offices)
    assert (row["district_address"], row["district_voice"], row["district_fax"]) == ("PO Box 3", "850-555-0200", "850-555-0201")
