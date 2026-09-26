"""Diagnostic only: reproduce filing parser failures for a fixed SEC sample."""
from __future__ import annotations
import os, traceback
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

TICKERS_CIK={
 "BA":"0000012927",
 "NOC":"0001133421",
 "CW":"0001333141",
 "PSBD":"0001906324",
 "QMCO":"0001835681",
}
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")

def main():
 r=SECXBRLSearchV2_3_8(user_agent=UA)
 for t,cik in TICKERS_CIK.items():
  print("\n===",t,cik,"===")
  try:
   sub=r.submissions(cik)
   recent=sub.get("filings",{}).get("recent",{})
   forms=recent.get("form") or []; filed=recent.get("filingDate") or []
   acc=recent.get("accessionNumber") or []; doc=recent.get("primaryDocument") or []
   candidates=[(filed[i] if i<len(filed) else "",forms[i],acc[i] if i<len(acc) else None,doc[i] if i<len(doc) else None) for i in range(len(forms)) if forms[i] in {"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}]
   print("annual_candidates",sorted(candidates,reverse=True)[:3])
   rows,meta=r._inline_filing_rows(cik,sub)
   print("SUCCESS rows=",len(rows),"meta=",meta)
  except Exception as exc:
   print("ERROR",type(exc).__name__,repr(exc))
   traceback.print_exc()
if __name__=="__main__": main()
