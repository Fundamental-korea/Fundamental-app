"""
Repair missing Korean WICS sector classifications.

Only fills Fundamental.wics_sector where it is currently NULL, using the same
WiseIndex WICS 10-sector source already used by collector.py. Existing
classifications are never overwritten.
"""
import os
from datetime import datetime, timezone
from collector import get_wics_sector_map
from supabase import create_client

URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
sb = create_client(URL, KEY)

rows = []
start = 0
while True:
    batch = (
        sb.table("Fundamental")
        .select("stock_code,stock_name,wics_sector")
        .is_("wics_sector", "null")
        .range(start, start + 199)
        .execute()
        .data or []
    )
    if not batch:
        break
    rows.extend(batch)
    if len(batch) < 200:
        break
    start += 200

print(f"Missing WICS before repair: {len(rows)}")
if not rows:
    raise SystemExit(0)

wics = get_wics_sector_map()
print(f"WICS map size: {len(wics)}")

updates = []
unresolved = []
for row in rows:
    code = row["stock_code"]
    sector = wics.get(code)
    if sector:
        updates.append({"stock_code": code, "wics_sector": sector})
    else:
        unresolved.append(row)

for i in range(0, len(updates), 100):
    batch = updates[i:i+100]
    sb.table("Fundamental").upsert(batch, on_conflict="stock_code").execute()
    print(f"Updated {min(i+100,len(updates))}/{len(updates)}")

print(f"Filled WICS: {len(updates)}")
print(f"Still unresolved: {len(unresolved)}")
for row in unresolved[:100]:
    print(f"UNRESOLVED {row['stock_code']} {row['stock_name']}")
