"""US market valuation snapshot helpers.

Market facts and SEC accounting facts are kept separate.

Formula contract:
- EPS: SEC-reported diluted EPS; never reconstruct from income/shares.
- BPS: parent-attributable common equity / period-end common shares outstanding.
- Market cap: current price * current common shares outstanding.
- PER: current price / latest full-year reported diluted EPS.
- PBR: current price / period-end BPS.
"""

from __future__ import annotations

import math


def _clean(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _fact_rows(companyfacts, tags, forms=None):
    forms = forms or {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
    root = companyfacts.get("facts") or {}
    out = []
    for namespace, namespace_tags in (("us-gaap", tags.get("us-gaap", [])), ("ifrs-full", tags.get("ifrs-full", []))):
        facts = root.get(namespace) or {}
        for priority, tag in enumerate(namespace_tags):
            fact = facts.get(tag)
            if not fact:
                continue
            for unit, rows in (fact.get("units") or {}).items():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if row.get("form") not in forms or not row.get("end"):
                        continue
                    value = _clean(row.get("val"))
                    if value is None:
                        continue
                    out.append({
                        "namespace": namespace,
                        "tag": tag,
                        "priority": priority,
                        "unit": unit,
                        "value": value,
                        "start": row.get("start"),
                        "end": row.get("end"),
                        "filed": row.get("filed") or "",
                        "fy": row.get("fy"),
                        "fp": row.get("fp"),
                        "frame": row.get("frame"),
                    })
    return out


def _best_instant(rows, end):
    candidates = [r for r in rows if not r.get("start") and r["end"] == end]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["namespace"] == "us-gaap", -r["priority"], r["filed"]), reverse=True)
    return candidates[0]


def _latest_instant(rows):
    if not rows:
        return None
    latest_end = max(r["end"] for r in rows)
    return _best_instant(rows, latest_end)


def _latest_full_year(rows):
    candidates = [
        r for r in rows
        if r.get("start") and r.get("days") is not None and 300 <= r["days"] <= 380
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["end"], r["filed"], r["namespace"] == "us-gaap", -r["priority"]), reverse=True)
    return candidates[0]


def _reported_eps(companyfacts):
    tags = {
        "us-gaap": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
        "ifrs-full": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    }
    rows = _fact_rows(companyfacts, tags)
    for row in rows:
        if row.get("start"):
            try:
                row["days"] = (__import__("datetime").date.fromisoformat(row["end"]) - __import__("datetime").date.fromisoformat(row["start"])).days
            except ValueError:
                row["days"] = None
    diluted = [r for r in rows if r["tag"] == "EarningsPerShareDiluted"]
    basic = [r for r in rows if r["tag"] == "EarningsPerShareBasic"]
    row = _latest_full_year(diluted)
    basis = "reported-diluted" if row else None
    if row is None:
        row = _latest_full_year(basic)
        basis = "reported-basic" if row else None
    return row, basis


def _equity_and_nci(companyfacts, fiscal_end):
    equity_tags = {
        "us-gaap": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "ifrs-full": ["EquityAttributableToOwnersOfParent", "Equity"],
    }
    nci_tags = {
        "us-gaap": ["MinorityInterest", "NoncontrollingInterestInConsolidatedEntity", "NoncontrollingInterestInConsolidatedEntityIncludingPortionAttributableToRedeemableNoncontrollingInterest"],
        "ifrs-full": ["NoncontrollingInterestsInEquity", "NoncontrollingInterestInConsolidatedEntity", "MinorityInterest"],
    }
    equity_rows = _fact_rows(companyfacts, equity_tags)
    nci_rows = _fact_rows(companyfacts, nci_tags)
    equity = _best_instant(equity_rows, fiscal_end)
    nci = _best_instant(nci_rows, fiscal_end)
    if equity is None:
        return None, None
    if equity["tag"] == "EquityAttributableToOwnersOfParent":
        return equity, None
    if "IncludingPortionAttributableToNoncontrollingInterest" in equity["tag"] and nci is not None:
        equity = dict(equity)
        equity["value"] -= nci["value"]
        equity["basis"] = "parent-attributable"
        equity["nci_source_tag"] = nci["tag"]
    return equity, nci


def _period_end_shares(companyfacts, fiscal_end):
    tags = {
        "us-gaap": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
        "ifrs-full": [],
    }
    rows = _fact_rows(companyfacts, tags)
    # For BPS we do not fall forward to a later date. That would mismatch the equity period.
    return _best_instant(rows, fiscal_end)


def _market_field(market_data, *keys):
    for key in keys:
        value = _clean((market_data or {}).get(key))
        if value is not None:
            return value
    return None


def build_valuation_snapshot(companyfacts, fiscal_end, market_data=None):
    market_data = market_data or {}
    eps_row, eps_basis = _reported_eps(companyfacts)
    equity_row, nci_row = _equity_and_nci(companyfacts, fiscal_end)
    period_shares_row = _period_end_shares(companyfacts, fiscal_end)

    price = _market_field(market_data, "price", "current_price", "regularMarketPrice")
    current_shares = _market_field(market_data, "current_shares", "shares_outstanding")
    if current_shares is None:
        # SEC fallback is only for current market-cap construction; if stale/ambiguous, upstream market source should override it.
        current_row = _latest_instant(_fact_rows(companyfacts, {"us-gaap": ["EntityCommonStockSharesOutstanding"], "ifrs-full": []}))
        current_shares = _clean(current_row["value"]) if current_row else None

    period_shares = _clean(period_shares_row["value"]) if period_shares_row else None
    equity = _clean(equity_row["value"]) if equity_row else None
    eps = _clean(eps_row["value"]) if eps_row else None

    bps = equity / period_shares if equity is not None and period_shares and period_shares > 0 else None
    market_cap = price * current_shares if price is not None and current_shares and current_shares > 0 else None
    per = price / eps if price is not None and eps is not None and eps > 0 else None
    pbr = price / bps if price is not None and bps is not None and bps > 0 else None

    result = {
        "price": price,
        "market_cap": market_cap,
        "current_shares_outstanding": current_shares,
        "current_shares_source": "market-data" if market_data.get("current_shares") is not None or market_data.get("shares_outstanding") is not None else ("sec-company-facts" if current_shares is not None else None),
        "period_end_shares_outstanding": period_shares,
        "period_end_shares_source": period_shares_row["tag"] if period_shares_row else None,
        "eps": eps,
        "eps_source": eps_row["tag"] if eps_row else None,
        "eps_basis": eps_basis,
        "bps": bps,
        "bps_basis": "parent-attributable-equity-period-end-shares" if bps is not None else None,
        "bps_equity": equity,
        "bps_equity_source": equity_row["tag"] if equity_row else None,
        "per": per,
        "per_basis": "current-price/latest-full-year-reported-eps" if per is not None else None,
        "pbr": pbr,
        "pbr_basis": "current-price/period-end-bps" if pbr is not None else None,
    }
    if equity_row and equity_row.get("basis"):
        result["bps_equity_basis"] = equity_row["basis"]
        result["bps_nci_source_tag"] = equity_row.get("nci_source_tag")
    elif nci_row and equity_row:
        result["bps_nci_note"] = "selected equity fact was not explicitly NCI-inclusive; no subtraction applied"

    for field, aliases in {
        "day_high": ("day_high", "regularMarketDayHigh"),
        "day_low": ("day_low", "regularMarketDayLow"),
        "week52_high": ("week52_high", "fiftyTwoWeekHigh"),
        "week52_low": ("week52_low", "fiftyTwoWeekLow"),
        "volume": ("volume", "regularMarketVolume"),
    }.items():
        value = _market_field(market_data, *aliases)
        if value is not None:
            result[field] = value
    return result


def normalize_market_quote(info, history=None):
    """Normalize yfinance quote information and one-year history."""
    info = info or {}
    result = {}
    mapping = {
        "price": ("currentPrice", "regularMarketPrice", "lastPrice"),
        "volume": ("regularMarketVolume", "volume"),
        "day_high": ("dayHigh", "regularMarketDayHigh"),
        "day_low": ("dayLow", "regularMarketDayLow"),
        "week52_high": ("fiftyTwoWeekHigh", "52WeekHigh"),
        "week52_low": ("fiftyTwoWeekLow", "52WeekLow"),
        "current_shares": ("sharesOutstanding", "impliedSharesOutstanding"),
    }
    for target, aliases in mapping.items():
        value = _market_field(info, *aliases)
        if value is not None:
            result[target] = value
    if history is not None and not history.empty:
        try:
            result.setdefault("price", float(history["Close"].iloc[-1]))
            result.setdefault("volume", float(history["Volume"].iloc[-1]))
            result["week52_high"] = float(history["High"].max())
            result["week52_low"] = float(history["Low"].min())
        except Exception:
            pass
    return result
