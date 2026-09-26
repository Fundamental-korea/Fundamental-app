"""Production collector dry-run for confirmed recoverability cases.

No Supabase writes. It calls the same build_result() used by production, with
SEC filing recovery enabled, then prints before/after critical metrics.
"""
from __future__ import annotations
import json,os
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from collector_us_fundamental import build_result,load_company

TICKERS=("NTIC","AQB","SB","BOW")
URL=os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY=os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY","")
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")

def main():
 if not KEY: raise RuntimeError("Supabase key required")
 from supabase import create_client
 sb=create_client(URL,KEY)
 rows=sb.table("US_Companies").select("ticker,cik,company_name,sector_common,company_type,scoring_profile").in_("ticker",list(TICKERS)).eq("is_fundamental_eligible",True).execute().data or []
 rowmap={r["ticker"]:r for r in rows}
 resolver=SECXBRLSearchV2_3_8(user_agent=UA)
 session=resolver.session
 for t in TICKERS:
  row=rowmap.get(t)
  print("\n===",t,"===")
  if not row:
   print("SKIP:not eligible or missing DB record"); continue
  try:
   facts,sub=load_company(session,t,row["cik"])
   result=build_result(t,row["cik"],row["company_name"],facts,sub,universe_row=row,
                       market_prices={"market":None,"stock":None},
                       filing_resolver=resolver,filing_recovery_cache={})
   avg=(result.get("period_scores") or {}).get("1y",{}).get("avg",{})
   ms=avg.get("metric_scores") or {}
   print(json.dumps({
     "base_year":result.get("base_year"),
     "data_reliability":result.get("data_reliability"),
     "missing_metric_count":result.get("missing_metric_count"),
     "roic":ms.get("roic"),
     "interest_coverage":ms.get("interest_coverage"),
     "eps_growth":ms.get("eps_growth"),
     "filing_recovery":result.get("filing_recovery"),
   },ensure_ascii=False,default=str))
  except Exception as exc:
   print("ERROR",type(exc).__name__,str(exc))
if __name__=="__main__":main()
