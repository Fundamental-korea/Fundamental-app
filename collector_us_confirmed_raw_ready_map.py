"""Inspect filing-map selections for confirmed raw-ready missing-metric cases."""
from __future__ import annotations
import json,os,traceback
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from sec_filing_financial_map import classify_filing_rows

TICKERS={
 "QMCO":"0001835681","LUNR":"0001782837","BOW":"0001551306",
 "AQB":"0000823469","SB":"0000081680","NPKI":"0000072237","CWD":"0001037387",
}
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")

def main():
 r=SECXBRLSearchV2_3_8(user_agent=UA)
 for t,cik in TICKERS.items():
  print("\n===",t,cik,"===")
  try:
   sub=r.submissions(cik)
   recent=sub.get("filings",{}).get("recent",{})
   forms=recent.get("form") or []; filed=recent.get("filingDate") or []; fys=recent.get("fy") or []
   best=None
   for i,form in enumerate(forms):
    if form not in {"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}: continue
    item=(filed[i] if i<len(filed) else "",fys[i] if i<len(fys) else None,form)
    if best is None or item[0]>(best[0] or ""): best=item
   year=int(best[1]) if best and best[1] is not None else None
   rows,meta=r._inline_filing_rows(cik,sub)
   mapped=classify_filing_rows(rows,target_year=year)
   def slim(x):
    if not x:return None
    return {"value":x.get("value"),"basis":x.get("basis"),"category":x.get("category"),
            "concept":x.get("concept"),"namespace":x.get("namespace"),"unit":x.get("unit"),
            "end":x.get("end"),"start":x.get("start"),"filed":x.get("filed"),
            "confidence":x.get("confidence")}
   print(json.dumps({
    "year":year,"form":best[2] if best else None,"filed":best[0] if best else None,
    "rows":len(rows),"roic_inputs":{
      "equity":slim(mapped.get("roic_inputs",{}).get("equity")),
      "cash":slim(mapped.get("roic_inputs",{}).get("cash")),
      "operating_income":slim(mapped.get("roic_inputs",{}).get("operating_income")),
    },
    "debt_status":mapped.get("debt_status"),
    "selected_debt":slim(mapped.get("selected_debt")),
    "interest_status":mapped.get("interest_status"),
    "selected_interest":slim(mapped.get("selected_interest")),
    "unclassified_debt":list((mapped.get("unclassified_like_counts",{}).get("debt") or {}).items())[:10],
    "unclassified_interest":list((mapped.get("unclassified_like_counts",{}).get("interest") or {}).items())[:10],
   },ensure_ascii=False))
  except Exception as e:
   print("ERROR",type(e).__name__,str(e));traceback.print_exc()
if __name__=="__main__":main()
