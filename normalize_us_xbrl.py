"""Normalize SEC Company Facts into US_Fundamental and US_XBRL_Facts.

The raw SEC JSON remains untouched in US_XBRL_Raw. This module creates two
layers:
1) US_XBRL_Facts: every numeric fact, preserving concept/unit/period/filing and dimensions.
2) US_Fundamental: a stable set of common financial-statement metrics for scoring/UI.

Because SEC filers use both US-GAAP and company-specific extension concepts,
the parser uses a prioritized alias map and records the selected source tags.
"""

import os
import re
from datetime import datetime, timezone

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def db():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is not set.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def parse_date(value):
    if not value:
        return None
    return str(value)[:10]


def numeric(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def concept_aliases():
    return {
        "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
        "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
        "gross_profit": ["GrossProfit"],
        "operating_income": ["OperatingIncomeLoss"],
        "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
        "net_income": ["NetIncomeLoss", "ProfitLoss"],
        "eps": ["EarningsPerShareBasic"],
        "diluted_eps": ["EarningsPerShareDiluted"],
        "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
        "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
        "accounts_receivable": ["AccountsReceivableNetCurrent"],
        "inventory": ["InventoryNet"],
        "current_assets": ["AssetsCurrent"],
        "property_plant_equipment": ["PropertyPlantAndEquipmentNet"],
        "goodwill": ["Goodwill"],
        "intangible_assets": ["FiniteLivedIntangibleAssetsNet", "IndefiniteLivedIntangibleAssetsExcludingGoodwill"],
        "total_assets": ["Assets"],
        "accounts_payable": ["AccountsPayableCurrent"],
        "current_liabilities": ["LiabilitiesCurrent"],
        "short_term_debt": ["ShortTermBorrowings", "ShortTermDebt"],
        "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"],
        "total_liabilities": ["Liabilities"],
        "total_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "shares_outstanding": ["EntityCommonStockSharesOutstanding", "CommonStocksIncludingAdditionalPaidInCapitalMember"],
        "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
        "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
        "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
        "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
        "stock_repurchases": ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfCommonAndPreferredStock"],
    }


def flatten_facts(ticker, cik, companyfacts):
    rows = []
    facts = companyfacts.get("facts", {}) or {}
    for taxonomy, namespace in facts.items():
        for concept, meta in (namespace or {}).items():
            label = meta.get("label")
            description = meta.get("description")
            for unit, observations in (meta.get("units") or {}).items():
                for fact in observations or []:
                    value = numeric(fact.get("val"))
                    if value is None:
                        continue
                    rows.append({
                        "ticker": ticker,
                        "cik": cik,
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "label": label,
                        "description": description,
                        "unit": unit,
                        "value": value,
                        "start_date": parse_date(fact.get("start")),
                        "end_date": parse_date(fact.get("end")),
                        "fiscal_year": fact.get("fy"),
                        "fiscal_period": fact.get("fp"),
                        "form": fact.get("form"),
                        "filed_date": parse_date(fact.get("filed")),
                        "accession_number": fact.get("accn"),
                        "frame": fact.get("frame"),
                        "dimensions": fact.get("dimensions") or {},
                        "raw_fact": fact,
                    })
    return rows


def score_fact(fact):
    # Prefer annual 10-K facts, then 10-Q, and prefer facts with a filing date.
    form = fact.get("form") or ""
    fp = fact.get("fiscal_period") or ""
    rank = 0
    if form in ("10-K", "20-F", "40-F"):
        rank += 100
    elif form == "10-Q":
        rank += 50
    if fp == "FY":
        rank += 20
    if fact.get("filed_date"):
        rank += 1
    return rank


def select_metric(rows, aliases, annual=True):
    candidates = []
    alias_rank = {name: len(aliases) - i for i, name in enumerate(aliases)}
    for row in rows:
        if row["concept"] not in alias_rank:
            continue
        # Do not use dimensional facts for the company-wide headline metrics.
        if row.get("dimensions"):
            continue
        if annual and row.get("fiscal_period") not in (None, "FY"):
            continue
        row_score = score_fact(row) * 1000 + alias_rank[row["concept"]]
        if row.get("end_date"):
            row_score += int(row["end_date"].replace("-", "")) / 100000000
        candidates.append((row_score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def build_statement_rows(ticker, cik, company_name, facts_rows):
    aliases = concept_aliases()
    by_period = {}
    for fact in facts_rows:
        end = fact.get("end_date")
        if not end:
            continue
        # Annual statement view. Keep each fiscal year/period separately.
        key = (fact.get("fiscal_year"), fact.get("fiscal_period"), end, fact.get("form"), fact.get("accession_number"))
        by_period.setdefault(key, []).append(fact)

    output = []
    for key, rows in by_period.items():
        fy, fp, end, form, acc = key
        if fp not in (None, "FY") and form not in ("10-K", "20-F", "40-F"):
            continue
        values = {}
        source_tags = {}
        for metric, names in aliases.items():
            selected = select_metric(rows, names, annual=False)
            values[metric] = selected["value"] if selected else None
            source_tags[metric] = {
                "taxonomy": selected["taxonomy"],
                "concept": selected["concept"],
                "unit": selected["unit"],
                "accession_number": selected["accession_number"],
            } if selected else None

        if all(v is None for v in values.values()):
            continue
        if values.get("operating_cash_flow") is not None and values.get("capital_expenditures") is not None:
            values["free_cash_flow"] = values["operating_cash_flow"] - abs(values["capital_expenditures"])
        else:
            values["free_cash_flow"] = None

        statement_type = "annual_financials"
        output.append({
            "ticker": ticker,
            "cik": cik,
            "company_name": company_name,
            "fiscal_year": fy or int(end[:4]),
            "fiscal_period": fp or "FY",
            "period_start": None,
            "period_end": end,
            "filed_date": rows[0].get("filed_date"),
            "form": form,
            "accession_number": acc,
            "statement_type": statement_type,
            **values,
            "source_tags": source_tags,
            "units": "native_xbrl_units",
            "data_reliability": "SEC_XBRL",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return output


def normalize_one(raw):
    ticker = raw["ticker"]
    cik = raw["cik"]
    companyfacts = raw.get("companyfacts") or {}
    submissions = raw.get("submissions") or {}
    company_name = raw.get("company_name") or companyfacts.get("entityName")
    facts_rows = flatten_facts(ticker, cik, companyfacts)

    client = db()
    for start in range(0, len(facts_rows), 500):
        client.table("US_XBRL_Facts").upsert(
            facts_rows[start:start + 500],
            on_conflict="ticker,taxonomy,concept,unit,value,start_date,end_date,fiscal_year,fiscal_period,form,accession_number,frame",
        ).execute()

    statement_rows = build_statement_rows(ticker, cik, company_name, facts_rows)
    for start in range(0, len(statement_rows), 500):
        client.table("US_Fundamental").upsert(
            statement_rows[start:start + 500],
            on_conflict="ticker,fiscal_year,fiscal_period,period_end,statement_type",
        ).execute()
    return len(facts_rows), len(statement_rows)


def normalize_all(limit=None):
    client = db()
    query = client.table("US_XBRL_Raw").select("*").order("ticker")
    if limit:
        query = query.limit(limit)
    raws = query.execute().data or []
    total_facts = 0
    total_statements = 0
    for raw in raws:
        try:
            facts, statements = normalize_one(raw)
            total_facts += facts
            total_statements += statements
            print(f"[NORMALIZE] {raw['ticker']}: facts={facts:,}, statements={statements:,}")
        except Exception as exc:
            print(f"[NORMALIZE] FAILED {raw.get('ticker')}: {exc}")
    print(f"[NORMALIZE] Completed. facts={total_facts:,}, statements={total_statements:,}")
    return total_facts, total_statements


if __name__ == "__main__":
    normalize_all()
