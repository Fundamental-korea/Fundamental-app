"""Filing-level XBRL fallback for utility extraction.

Downloads the latest annual SEC filing's XBRL instance and label linkbase in
memory and converts standard/custom concepts into compact filing-xbrl facts.
Nothing is stored.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import requests

INSTANCE_EXCLUDE={"filingsummary.xml","filingsummary.json","metalinks.json"}
ANNUAL_FORMS={"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}
XLINK="http://www.w3.org/1999/xlink"


def _local(tag): return tag.rsplit("}",1)[-1]
def _namespace(tag): return tag[1:].split("}",1)[0] if tag.startswith("{") and "}" in tag else ""
def _date(v):
    if not v:return None
    from datetime import date
    try:return date.fromisoformat(str(v)[:10])
    except ValueError:return None

def _duration_days(start,end):
    s,e=_date(start),_date(end)
    return (e-s).days if s and e else None

def _is_instance_candidate(name):
    low=name.lower()
    return low.endswith(".xml") and low not in INSTANCE_EXCLUDE and not any(x in low for x in ("_cal.xml","_def.xml","_lab.xml","_pre.xml","_ref.xml"))

def _accession(submissions):
    recent=submissions.get("filings",{}).get("recent",{})
    for i,form in enumerate(recent.get("form",[])):
        if form in ANNUAL_FORMS:
            acc=recent.get("accessionNumber",[])[i] if i<len(recent.get("accessionNumber",[])) else None
            doc=recent.get("primaryDocument",[])[i] if i<len(recent.get("primaryDocument",[])) else None
            if acc:return acc,doc
    return None,None

def _filing_index(session,cik,accession):
    compact=accession.replace("-","")
    url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/index.json"
    r=session.get(url,timeout=30);r.raise_for_status()
    return r.json(),compact

def _choose_instance(index_json,primary_document):
    names=[x.get("name") for x in index_json.get("directory",{}).get("item",[]) if x.get("name")]
    candidates=[n for n in names if _is_instance_candidate(n)]
    stem=re.sub(r"\.[^.]+$","",primary_document or "").lower()
    p=[n for n in candidates if stem and n.lower().startswith(stem)]
    if p:return p[0]
    p=[n for n in candidates if "instance" in n.lower() or "xbrl" in n.lower()]
    if p:return p[0]
    return max(candidates,key=len) if candidates else None

def _choose_label(index_json):
    names=[x.get("name") for x in index_json.get("directory",{}).get("item",[]) if x.get("name")]
    c=[n for n in names if n and n.lower().endswith("_lab.xml")]
    return c[0] if c else None

def _parse_labels(xml_text):
    """Return concept-id -> preferred human label text from an XBRL label linkbase."""
    root=ET.fromstring(xml_text)
    locs={};labels={};rels=[]
    standard_roles=(
        "http://www.xbrl.org/2003/role/label",
        "http://www.xbrl.org/2003/role/terseLabel",
        "http://www.xbrl.org/2003/role/verboseLabel",
    )
    for e in root.iter():
        n=_local(e.tag)
        if n=="loc":
            label=e.attrib.get(f"{{{XLINK}}}label") or e.attrib.get("label")
            href=e.attrib.get(f"{{{XLINK}}}href") or e.attrib.get("href")
            if label and href:locs[label]=href.split("#")[-1]
        elif n=="label":
            label=e.attrib.get(f"{{{XLINK}}}label") or e.attrib.get("label")
            role=e.attrib.get(f"{{{XLINK}}}role") or e.attrib.get("role") or ""
            text=" ".join("".join(e.itertext()).split())
            if label and text:
                priority=2 if role==standard_roles[0] else 1 if role in standard_roles[1:] else 0
                labels[label]=(priority,text)
        elif n=="labelArc":
            frm=e.attrib.get(f"{{{XLINK}}}from") or e.attrib.get("from")
            to=e.attrib.get(f"{{{XLINK}}}to") or e.attrib.get("to")
            if frm and to:rels.append((frm,to))
    out={}
    for frm,to in rels:
        concept=locs.get(frm);item=labels.get(to)
        if concept and item:
            priority,text=item
            prev=out.get(concept)
            if prev is None or priority>prev[0]:out[concept]=(priority,text)
    return {k:v[1] for k,v in out.items()}

def _context_has_dimension(context):
    """Return True when an XBRL context contains an explicit or typed dimension."""
    for e in context.iter():
        if _local(e.tag) in {"explicitMember","typedMember"}:
            return True
    return False

def _semantic_aliases(local,label=""):
    """Map custom concept names/labels to extractor candidate tags."""
    s=re.sub(r"[^a-z0-9]","",local.lower())
    l=re.sub(r"[^a-z0-9]","",label.lower())
    aliases=[]
    combined=s+" "+l
    if (
        "proprietarycapital" in combined
        or "totalproprietarycapital" in combined
        or "proprietaryfundcapital" in combined
        or "totalshareholdersequity" in combined
        or "totalstockholdersequity" in combined
        or "equity" in s
    ):
        aliases += ["TotalProprietaryCapital","ProprietaryCapital","Equity","StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
    if "earningspershare" in s or "eps" in s or "earningspershare" in l or "pershare" in l:
        aliases += ["EarningsPerShareDiluted","EarningsPerShareBasic","EarningsPerShareBasicAndDiluted"]
    if "dividend" in s or "dividend" in l or "commonshareholderdividend" in l or "commonstockdividend" in l:
        aliases += ["PaymentsOfDividendsCommonStock","PaymentsOfOrdinaryDividends","DividendsPaid","PaymentsOfDividends"]
    if "longtermdebt" in s and "current" in s:aliases += ["LongTermDebtCurrent"]
    elif "longtermdebt" in s or ("debt" in s and "noncurrent" in s):aliases += ["LongTermDebtNoncurrent"]
    elif "borrowings" in s and "current" in s:aliases += ["CurrentBorrowings"]
    elif "borrowings" in s:aliases += ["Borrowings"]
    if "operatingcashflow" in s or "netcashprovided" in s or "cashflowfromoperating" in l:aliases += ["NetCashProvidedByUsedInOperatingActivities"]
    if "interest" in s and ("expense" in s or "financecost" in s):aliases += ["InterestAndDebtExpense","InterestExpense"]
    if "capitalexpenditure" in s or "capex" in s or ("propertyplantandequipment" in s and "payment" in s):aliases += ["PaymentsToAcquirePropertyPlantAndEquipment"]
    if "operatingincome" in s or "operatingincome" in l:aliases += ["OperatingIncomeLoss"]
    if "revenue" in s and "operating" in s:aliases += ["RegulatedAndUnregulatedOperatingRevenue","RegulatedOperatingRevenue","Revenues"]
    if ("netincome" in s or "profitloss" in s) and ("common" in s or "shareholder" in s or "parent" in s):aliases += ["NetIncomeLossAttributableToCommonStockholders","NetIncomeLossAttributableToParent"]
    if any(x in combined for x in ("dilutedearningspershare","basicearningspershare","earningspershare")):
        aliases += ["EarningsPerShareDiluted","EarningsPerShareBasic","EarningsPerShareBasicAndDiluted"]
    return list(dict.fromkeys(aliases))

def _parse_instance(xml_text,label_map=None):
    root=ET.fromstring(xml_text);contexts={};units={};facts={}
    label_map=label_map or {}
    for e in root.iter():
        local=_local(e.tag)
        if local=="context":
            cid=e.attrib.get("id")
            if not cid:continue
            instant=start=end=None
            for c in e.iter():
                n=_local(c.tag);t=(c.text or "").strip()
                if n=="instant" and t:instant=t
                elif n=="startDate" and t:start=t
                elif n=="endDate" and t:end=t
            contexts[cid]={"instant":instant,"start":start,"end":end,"has_dimension":_context_has_dimension(e)}
        elif local=="unit":
            uid=e.attrib.get("id")
            if uid:
                measure=None
                for c in e.iter():
                    if _local(c.tag)=="measure" and (c.text or "").strip():measure=(c.text or "").strip();break
                units[uid]=measure
    for e in root.iter():
        cref=e.attrib.get("contextRef")
        if not cref:continue
        ctx=contexts.get(cref);local=_local(e.tag);ns=_namespace(e.tag)
        if not ctx or not ns or local in {"context","unit"}:continue
        text=(e.text or "").strip()
        try:val=float(text.replace(",",""))
        except ValueError:continue
        row={"val":val,"form":"10-K","filed":"","frame":None,"fy":None,"end":ctx.get("instant") or ctx.get("end"),"start":ctx.get("start"),"filing_annual":bool(ctx.get("start") and ctx.get("end")),"has_dimension":bool(ctx.get("has_dimension")),"source_tag":local,"label":label_map.get(local,"")}
        row["days"]=_duration_days(ctx.get("start"),ctx.get("end")) if row["filing_annual"] else None
        unit=units.get(e.attrib.get("unitRef")) or "USD"
        label=row["label"]
        for alias in _semantic_aliases(local,label):facts.setdefault(alias,[]).append((unit,row.copy()))
        facts.setdefault(local,[]).append((unit,row.copy()))
    return facts

def augment_with_latest_filing(session:requests.Session,cik,submissions,facts):
    try:
        accession,doc=_accession(submissions)
        if not accession:return facts,{"used":False,"reason":"no_annual_filing"}
        idx,compact=_filing_index(session,cik,accession);instance=_choose_instance(idx,doc)
        if not instance:return facts,{"used":False,"reason":"no_xbrl_instance","accession":accession}
        label_file=_choose_label(idx);label_map={}
        if label_file:
            lurl=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{label_file}"
            lr=session.get(lurl,timeout=45);lr.raise_for_status();label_map=_parse_labels(lr.text)
        url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{instance}"
        r=session.get(url,timeout=45);r.raise_for_status();parsed=_parse_instance(r.text,label_map)
        merged=dict(facts);root=dict((facts or {}).get("facts",facts or {}));fx=root.setdefault("filing-xbrl",{})
        for tag,rows in parsed.items():
            units=fx.setdefault(tag,{}).setdefault("units",{})
            for unit,row in rows:units.setdefault(unit,[]).append(row)
        merged["facts"]=root
        return merged,{"used":True,"accession":accession,"primary_document":doc,"instance":instance,"label_file":label_file,"labeled_concepts":len(label_map),"concept_count":len(parsed)}
    except Exception as exc:
        return facts,{"used":False,"reason":type(exc).__name__,"error":str(exc)[:240]}
