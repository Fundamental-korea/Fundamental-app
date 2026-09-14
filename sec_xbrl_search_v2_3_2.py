"""SEC XBRL resolver V2.3.2: robust SEC label XML parsing."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from sec_xbrl_search_v2_3_1 import SECXBRLSearchV2_3_1

XLINK = "http://www.w3.org/1999/xlink"

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

class SECXBRLSearchV2_3_2(SECXBRLSearchV2_3_1):
    def _parse_labels(self, xml_text):
        payload = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
        root = ET.fromstring(payload)
        locs, labels, rels = {}, {}, []
        standard = "http://www.xbrl.org/2003/role/label"
        for e in root.iter():
            n = _local(e.tag)
            if n == "loc":
                k = e.attrib.get(f"{{{XLINK}}}label") or e.attrib.get("label")
                href = e.attrib.get(f"{{{XLINK}}}href") or e.attrib.get("href")
                if k and href: locs[k] = href.split("#")[-1]
            elif n == "label":
                k = e.attrib.get(f"{{{XLINK}}}label") or e.attrib.get("label")
                role = e.attrib.get(f"{{{XLINK}}}role") or e.attrib.get("role") or ""
                text = " ".join("".join(e.itertext()).split())
                if k and text: labels[k] = (2 if role == standard else 1 if "Label" in role else 0, text)
            elif n == "labelArc":
                frm = e.attrib.get(f"{{{XLINK}}}from") or e.attrib.get("from")
                to = e.attrib.get(f"{{{XLINK}}}to") or e.attrib.get("to")
                if frm and to: rels.append((frm, to))
        out = {}
        for frm, to in rels:
            concept, item = locs.get(frm), labels.get(to)
            if concept and item and (concept not in out or item[0] > 0): out[concept] = item[1]
        return out

SECXBRLSearchV2_3 = SECXBRLSearchV2_3_2
SECXBRLSearchV2 = SECXBRLSearchV2_3_2
