"""US market valuation snapshot helpers.

Keeps market-derived values separate from SEC accounting facts.

Rules:
- EPS: use the SEC-reported diluted EPS fact; never synthesize EPS from net income/shares.
- BPS: parent-attributable common equity / period-end common shares outstanding.
- Market cap: current price * current common shares outstanding.
- PER: current price / selected reported EPS.
- PBR: current price / period-end BPS.

The helper is deliberately conservative: ambiguous share-count facts are left out
rather than guessing across multiple common-share classes.
"""

from __future__ import annotations

import math
from datetime import datetime


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


def _best_instant(rows, end=None):
    candidates = [r for r in rows if not r.get("start") and (end is None or r["end"] == end)]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["namespace"] == "us-gaap", -r["priority"], r["filed"], r["end"]), reverse=True)
    return candidates[0]


def _latest_instant(rows):
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (r["end"], r["filed"], r["namespace"] == "us-gaap", -r["priority"]), reverse=True)
    return _best_instant(rows, rows[0]["end"])


def _reported_eps(companyfacts):
    tags = {
        "us-gaap": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
        "ifrs-full": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    }
    rows = _fact_rows(companyfacts, tags)
    diluted = [r for r in rows if r["tag"] == "EarningsPerShareDiluted"]
    basic = [r for r in rows if r["tag"] == "EarningsPerShareBasic"]
    # Prefer the latest reported diluted EPS. Fall back to basic EPS only if diluted is absent.
    row = sorted(diluted, key=lambda r: (r["end"], r["filed"], r["namespace"] == "us-gaap"), reverse=True)[0] if diluted else None
    if row is None and basic:
        row = sorted(basic, key=lambda r: (r["end"], r["filed"], r["namespace"] == "us-gaap"), reverse=True)[0]
    return row


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
    if equity is None:
        equity = _latest_instant(equity_rows)
    nci = _best_instant(nci_rows, fiscal_end)
    if equity is None:
        return None, None
    # IFRS parent-equity tags are already parent-attributable.
    if equity["tag"] == "EquityAttributableToOwnersOfParent":
        return equity, None
    # US GAAP tag explicitly says that NCI is included: subtract matching NCI.
    if "IncludingPortionAttributableToNoncontrollingInterest" in equity["tag"] and nci is not None:
        equity = dict(equity)
        equity["value"] = equity["value"] - nci["value"]
        equity["basis"] = "parent-attributable"
        equity["nci_source_tag"] = nci["tag"]
        return equity, nci
    return equity, nci


def _shares(companyfacts, fiscal_end=None, current_only=False):
    tags = {
        "us-gaap": ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"],
        "ifrs-full": [],
    }
    rows = _fact_rows(companyfacts, tags)
    if fiscal_end:
        row = _best_instant(rows, fiscal_end)
        if row:
            return row
    return _latest_instant(rows)


def _market_field(market_data, *keys):
    for key in keys:
        value = _clean((market_data or {}).get(key))
        if value is not None:
            return value
    return None


def build_valuation_snapshot(companyfacts, fiscal_end, market_data=None):
    market_data = market_data or {}
    eps_row = _reported_eps(companyfacts)
    equity_row, nci_row = _equity_and_nci(companyfacts, fiscal_end)
    period_shares_row = _shares(companyfacts, fiscal_end)
    current_shares_row = _shares(companyfacts)

    price = _market_field(market_data, "price", "current_price", "regularMarketPrice")
    current_shares = _clean(current_shares_row["value"]) if current_shares_row else None
    period_shares = _clean(period_shares_row["value"]) if period_shares_row else None
    equity = _clean(equity_row["value"]) if equity_row else None
    eps = _clean(eps_row["value"]) if eps_row else None

    bps = None
    if equity is not None and period_shares and period_shares > 0:
        bps = equity / period_shares

    market_cap = None
    if price is not None and current_shares and current_shares > 0:
        market_cap = price * current_shares

    per = None
    if price is not None and eps is not None and eps > 0:
        per = price / eps

    pbr = None
    if price is not None and bps is not None and bps > 0:
        pbr = price / bps

    result = {
        "price": price,
        "market_cap": market_cap,
        "current_shares_outstanding": current_shares,
        "current_shares_source": current_shares_row["tag"] if current_shares_row else None,
        "period_end_shares_outstanding": period_shares,
        "period_end_shares_source": period_shares_row["tag"] if period_shares_row else None,
        "eps": eps,
        "eps_source": eps_row["tag"] if eps_row else None,
        "eps_basis": "reported-diluted" if eps_row and eps_row["tag"] == "EarningsPerShareDiluted" else ("reported-basic" if eps_row else None),
        "bps": bps,
        "bps_basis": "parent-attributable-equity-period-end-shares" if bps is not None else None,
        "bps_equity": equity,
        "bps_equity_source": equity_row["tag"] if equity_row else None,
        "per": per,
        "per_basis": "current-price/reported-eps" if per is not None else None,
        "pbr": pbr,
        "pbr_basis": "current-price/period-end-bps" if pbr is not None else None,
    }
    if equity_row and equity_row.get("basis"):
        result["bps_equity_basis"] = equity_row["basis"]
        result["bps_nci_source_tag"] = equity_row.get("nci_source_tag")
    elif nci_row and equity_row:
        result["bps_nci_note"] = "selected equity tag was not explicitly NCI-inclusive; no subtraction applied"
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
    """Normalize yfinance quote/1y history into stable scalar market fields."""
    info = info or {}
    result = {}
    mapping = {
        "price": ("currentPrice", "regularMarketPrice", "lastPrice"),
        "volume": ("regularMarketVolume", "volume"),
        "day_high": ("dayHigh", "regularMarketDayHigh"),
        "day_low": ("dayLow", "regularMarketDayLow"),
        "week52_high": ("fiftyTwoWeekHigh", "52WeekHigh"),
        "week52_low": ("fiftyTwoWeekLow", "52WeekLow"),
        "market_cap": ("marketCap",),
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
