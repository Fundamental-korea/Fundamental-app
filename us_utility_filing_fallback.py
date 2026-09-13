"""Filing-level XBRL fallback for utility extraction.

Downloads the latest annual SEC filing's XBRL instance in memory and converts
standard/custom concepts into a compact filing-xbrl fact namespace. Nothing
is stored.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import requests

INSTANCE_EXCLUDE = {"filingsummary.xml", "filingsummary.json", "metalinks.json"}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def _local(tag): return tag.rsplit("}", 1)[-1]
def _namespace(tag): return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""
def _date(v):
    if not v: return None
    from datetime import date
    try: return date.fromisoformat(str(v)[:10])
    except ValueError: return None

def _duration_days(start, end):
    s,e=_date(start),_date(end)
    return (e-s).days if s and e else None

def _is_instance_candidate(name):
    low=name.lower()
    return low.endswith(".xml") and low not in INSTANCE_EXCLUDE and not any(x in low for x in ("_cal.xml","_def.xml","_lab.xml","_pre.xml","_ref.xml"))

def _accession(submissions):
    recent=submissions.get("filings",{}).get("recent",{})
    for i,form in enumerate(recent.get("form",[])):
        if form in ANNUAL_FORMS:
            acc=recent.get("accessionNumber",[])[i] if i < len(recent.get("accessionNumber",[])) else None
            doc=recent.get("primaryDocument",[])[i] if i < len(recent.get("primaryDocument",[])) else None
            if acc: return acc,doc
    return None,None

def _filing_index(session,cik,accession):
    compact=accession.replace("-","")
    url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/index.json"
    r=session.get(url,timeout=30); r.raise_for_status()
    return r.json(),compact

def _choose_instance(index_json,primary_document):
    names=[x.get("name") for x in index_json.get("directory",{}).get("item",[]) if x.get("name")]
    candidates=[n for n in names if _is_instance_candidate(n)]
    stem=re.sub(r"\.[^.]+$","",primary_document or "").lower()
    p=[n for n in candidates if stem and n.lower().startswith(stem)]
    if p: return p[0]
    p=[n for n in candidates if "instance" in n.lower() or "xbrl" in n.lower()]
    if p: return p[0]
    return max(candidates,key=len) if candidates else None

def _semantic_aliases(local):
    """Map common custom utility concepts to extractor candidate tags."""
    s=re.sub(r"[^a-z0-9]","",local.lower())
    aliases=[]
    if "equity" in s:
        aliases += ["Equity","StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
    if "earningspershare" in s or s.endswith("eps") or "eps" in s:
        aliases += ["EarningsPerShareDiluted","EarningsPerShareBasic","EarningsPerShareBasicAndDiluted"]
    if "dividend" in s:
        aliases += ["PaymentsOfDividendsCommonStock","PaymentsOfOrdinaryDividends","DividendsPaid","PaymentsOfDividends"]
    if "longtermdebt" in s and "current" in s: aliases += ["LongTermDebtCurrent"]
    elif "longtermdebt" in s or ("debt" in s and "noncurrent" in s): aliases += ["LongTermDebtNoncurrent"]
    elif "borrowings" in s and "current" in s: aliases += ["CurrentBorrowings"]
    elif "borrowings" in s: aliases += ["Borrowings"]
    if "operatingcashflow" in s or "netcashprovided" in s: aliases += ["NetCashProvidedByUsedInOperatingActivities"]
    if "interest" in s and ("expense" in s or "financecost" in s): aliases += ["InterestAndDebtExpense","InterestExpense"]
    if "capitalexpenditure" in s or "capex" in s or ("propertyplantandequipment" in s and "payment" in s): aliases += ["PaymentsToAcquirePropertyPlantAndEquipment"]
    if "operatingincome" in s: aliases += ["OperatingIncomeLoss"]
    if "revenue" in s and "operating" in s: aliases += ["RegulatedAndUnregulatedOperatingRevenue","RegulatedOperatingRevenue","Revenues"]
    if ("netincome" in s or "profitloss" in s) and ("common" in s or "shareholder" in s or "parent" in s): aliases += ["NetIncomeLossAttributableToCommonStockholders","NetIncomeLossAttributableToParent"]
    return list(dict.fromkeys(aliases))

def _parse_instance(xml_text):
    root=ET.fromstring(xml_text); contexts={}; units={}; facts={}
    for e in root.iter():
        local=_local(e.tag)
        if local=="context":
            cid=e.attrib.get("id")
            if not cid: continue
            instant=start=end=None
            for c in e.iter():
                n=_local(c.tag); t=(c.text or "").strip()
                if n=="instant" and t: instant=t
                elif n=="startDate" and t: start=t
                elif n=="endDate" and t: end=t
            contexts[cid]={"instant":instant,"start":start,"end":end}
        elif local=="unit":
            uid=e.attrib.get("id")
            if uid:
                measure=None
                for c in e.iter():
                    if _local(c.tag)=="measure" and (c.text or "").strip(): measure=(c.text or "").strip(); break
                units[uid]=measure
    for e in root.iter():
        cref=e.attrib.get("contextRef")
        if not cref: continue
        ctx=contexts.get(cref); local=_local(e.tag); ns=_namespace(e.tag)
        if not ctx or not ns or local in {"context","unit"}: continue
        text=(e.text or "").strip()
        try: val=float(text.replace(",",""))
        except ValueError: continue
        row={"val":val,"form":"10-K","filed":"","frame":None,"fy":None,"end":ctx.get("instant") or ctx.get("end"),"start":ctx.get("start"),"filing_annual":bool(ctx.get("start") and ctx.get("end"))}
        row["days"]=_duration_days(ctx.get("start"),ctx.get("end")) if row["filing_annual"] else None
        unit=units.get(e.attrib.get("unitRef")) or "USD"
        for alias in _semantic_aliases(local): facts.setdefault(alias,[]).append((unit,row.copy()))
        facts.setdefault(local,[]).append((unit,row.copy()))
    return facts

def augment_with_latest_filing(session: requests.Session,cik,submissions,facts):
    try:
        accession,doc=_accession(submissions)
        if not accession: return facts,{"used":False,"reason":"no_annual_filing"}
        idx,compact=_filing_index(session,cik,accession); instance=_choose_instance(idx,doc)
        if not instance: return facts,{"used":False,"reason":"no_xbrl_instance","accession":accession}
        url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{instance}"
        r=session.get(url,timeout=45); r.raise_for_status(); parsed=_parse_instance(r.text)
        merged=dict(facts); root=dict((facts or {}).get("facts",facts or {})); fx=root.setdefault("filing-xbrl",{})
        for tag,rows in parsed.items():
            units=fx.setdefault(tag,{}).setdefault("units",{})
            for unit,row in rows: units.setdefault(unit,[]).append(row)
        merged["facts"]=root
        return merged,{"used":True,"accession":accession,"primary_document":doc,"instance":instance,"concept_count":len(parsed)}
    except Exception as exc:
        return facts,{"used":False,"reason":type(exc).__name__,"error":str(exc)[:240]}
