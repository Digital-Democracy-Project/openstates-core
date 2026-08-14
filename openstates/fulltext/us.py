import typing

from lxml import etree

from .common import Metadata


# US federal bill XML (govinfo.gov) actually comes in two different schemas depending on
# bill stage, confirmed directly against real bills fetched 2026-08-12: most stages (Introduced,
# Engrossed, Reported, etc.) use the older "bill.dtd"-based format (root element <bill>, a
# <metadata><dublinCore> sidecar, body text under <legis-body>/<resolution-body>), while
# Enrolled Bills (and likely Public Law/Statute stages) use the newer USLM XML schema (root
# element <bill> in the "http://schemas.gpo.gov/xml/uslm" namespace, metadata under <meta>).
# Rather than detect and branch on the schema, walking every text node via itertext() -- the
# same aggressive-but-effective approach already used for Utah's XML (`handle_utah_xml`) and
# Washington/Texas's bare HTML -- works correctly against both, verified directly.
def handle_us_bill_xml(data: bytes, metadata: Metadata) -> str:
    """
    Extract readable text from a US federal bill XML document (govinfo.gov).

    Deliberately strips the <metadata>/<meta> sidecar block (Dublin Core fields in the legacy
    format, USLM's <meta> in the newer one) before extracting text: fields like the conversion
    timestamp change on every re-export even when the bill's actual text hasn't changed, which
    would inject spurious diff noise on every version transition -- the same boilerplate-noise
    failure mode already found and fixed for FL/VA/WA/AZ (OPEN-7/8/9/10). The rest of the
    document (form/preface + legis-body/main) is walked in full via itertext(), matching the
    same trade-off already accepted for Utah/Washington/Texas: real content is captured
    correctly at the cost of the original document's exact visual layout, which `raw_text` was
    never expected to preserve.
    """
    parser = etree.XMLParser(
        recover=True, resolve_entities=False, no_network=True, load_dtd=False
    )
    root = etree.fromstring(data, parser=parser)
    if root is None:
        raise ValueError("US bill XML did not parse even with recovery enabled")

    def _local_name(el: typing.Any) -> typing.Optional[str]:
        tag = el.tag
        if not isinstance(tag, str):
            return None  # comments/processing instructions have non-string tags
        return etree.QName(tag).localname

    for el in list(root.iter()):
        if _local_name(el) in ("metadata", "meta"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    parts: typing.List[str] = [t.strip() for t in root.itertext() if t and t.strip()]
    return " ".join(parts)
