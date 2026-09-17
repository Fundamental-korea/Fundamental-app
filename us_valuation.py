"""US market valuation snapshot helpers."""

from __future__ import annotations

import math
import re
from datetime import date

SEC_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def _clean(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _fact_rows(companyfacts, tags, forms=None):
    forms = forms or SEC_FORMS
    root = companyfacts.get("facts") or {}
    out = []
    for namespace, namespace_tags in tags.items():
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
                    days = None
                    if row.get("start"):
                        try:
                            days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                        except ValueError:
                            pass
                    out.append({"namespace": namespace, "tag": tag, "priority": priority, "unit": unit, "value": value, "start": row.get("start"), "end": row["end"], "filed": row.get("filed") or "", "fy": row.get("fy"), "fp": row.get("fp"), "frame": row.get("frame"), "days": days})
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
    candidates = [r for r in rows if r.get("start") and r.get("days") is not None and 300 <= r["days"] <= 380]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["end"], r["filed"], r["namespace"] == "us-gaap", -r["priority"]), reverse=True)
    return candidates[0]


def _reported_eps(companyfacts):
    tags = {"us-gaap": ["EarningsPerShareDiluted", "EarningsPerShareBasic"], "ifrs-full": ["EarningsPerShareDiluted", "EarningsPerShareBasic"]}
    rows = _fact_rows(companyfacts, tags)
    diluted = [r for r in rows if r["tag"] == "EarningsPerShareDiluted"]
    basic = [r for r in rows if r["tag"] == "EarningsPerShareBasic"]
    row = _latest_full_year(diluted)
    basis = "reported-diluted" if row else None
    if row is None:
        row = _latest_full_year(basic)
        basis = "reported-basic" if row else None
    return row, basis


def _equity_and_nci(companyfacts, fiscal_end):
    equity_tags = {"us-gaap": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], "ifrs-full": ["EquityAttributableToOwnersOfParent", "Equity"]}
    nci_tags = {"us-gaap": ["MinorityInterest", "NoncontrollingInterestInConsolidatedEntity", "NoncontrollingInterestInConsolidatedEntityIncludingPortionAttributableToRedeemableNoncontrollingInterest"], "ifrs-full": ["NoncontrollingInterestsInEquity", "NoncontrollingInterestInConsolidatedEntity", "MinorityInterest"]}
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


def _dei_share_rows(companyfacts):
    return _fact_rows(companyfacts, {"dei": ["EntityCommonStockSharesOutstanding"]})


def _sum_dei_shares_on_date(rows, end):
    candidates = [r for r in rows if r["end"] == end and not r.get("start")]
    if not candidates:
        return None
    return {"value": sum(r["value"] for r in candidates), "end": end, "filed": max(r["filed"] for r in candidates), "tag": "dei:EntityCommonStockSharesOutstanding", "namespace": "dei", "basis": "sum-of-common-classes"}


def _period_end_shares(companyfacts, fiscal_end):
    gaap_rows = _fact_rows(companyfacts, {"us-gaap": ["CommonStockSharesOutstanding"]})
    exact = _best_instant(gaap_rows, fiscal_end)
    if exact is not None:
        return exact, "exact-period-end"
    dei_rows = _dei_share_rows(companyfacts)
    exact_dei = _sum_dei_shares_on_date(dei_rows, fiscal_end)
    if exact_dei is not None:
        return exact_dei, "exact-period-end-dei"
    try:
        target = date.fromisoformat(fiscal_end)
    except ValueError:
        return None, None
    future_dates = sorted({r["end"] for r in dei_rows if not r.get("start") and r["end"] > fiscal_end})
    for end in future_dates:
        delta = (date.fromisoformat(end) - target).days
        if 0 < delta <= 120:
            row = _sum_dei_shares_on_date(dei_rows, end)
            if row is not None:
                row["basis"] = "nearest-subsequent-cover-date"
                row["days_after_fiscal_end"] = delta
                return row, "nearest-subsequent-dei"
        if delta > 120:
            break
    return None, None


def parse_common_shares_from_filing(text, fiscal_end=None):
    """Extract common-share counts from SEC filing cover/table text when Company Facts does not expose them."""
    if not text:
        return None
    clean = re.sub(r"\s+", " ", text)
    # Table-style "Class A ... Outstanding ... 450,571,783" or "Class B ... Outstanding ... 400".
    patterns = [
        re.compile(r"Class\s+[A-Z][^\.]{0,220}?Outstanding\s*[–-]\s*(\d[\d,]*)", re.I),
        re.compile(r"Class\s+[A-Z][^\.]{0,220}?outstanding\s*(?:shares?)?\s*(?:of)?\s*(\d[\d,]*)", re.I),
    ]
    values = []
    for pattern in patterns:
        for match in pattern.finditer(clean):
            n = int(match.group(1).replace(",", ""))
            if n > 0:
                values.append(n)
    # Cover-page wording: "there were X shares ... Class A ... outstanding and Y shares ... Class B ... outstanding"
    if not values:
        cover = re.search(r"there were\s+(\d[\d,]*)\s+shares.*?Class A.*?outstanding.*?(?:and|,).*?(\d[\d,]*)\s+shares.*?Class B.*?outstanding", clean, re.I)
        if cover:
            values = [int(cover.group(1).replace(",", "")), int(cover.group(2).replace(",", ""))]
    if not values:
        return None
    # Deduplicate accidental matches while preserving counts.
    values = list(dict.fromkeys(values))
    return {"value": sum(values), "tag": "filing-cover-common-shares", "namespace": "filing", "basis": "filing-text-sum-of-common-classes", "class_count": len(values), "fiscal_end": fiscal_end}


def _market_field(market_data, *keys):
    for key in keys:
        value = _clean((market_data or {}).get(key))
        if value is not None:
            return value
    return None


def build_valuation_snapshot(companyfacts, fiscal_end, market_data=None, filing_shares=None):
    market_data = market_data or {}
    eps_row, eps_basis = _reported_eps(companyfacts)
    equity_row, nci_row = _equity_and_nci(companyfacts, fiscal_end)
    period_shares_row, period_shares_basis = _period_end_shares(companyfacts, fiscal_end)
    if period_shares_row is None and filing_shares is not None:
        period_shares_row = filing_shares
        period_shares_basis = "filing-cover-fallback"

    price = _market_field(market_data, "price", "current_price", "regularMarketPrice")
    current_shares = _market_field(market_data, "current_shares", "shares_outstanding")
    current_shares_basis = "market-data" if current_shares is not None else None
    if current_shares is None:
        current_row = _current_shares_from_sec(companyfacts)
        current_shares = _clean(current_row["value"]) if current_row else None
        current_shares_basis = "sec-dei-latest-cover-date" if current_shares is not None else None
    if current_shares is None and filing_shares is not None:
        current_shares = _clean(filing_shares.get("value"))
        current_shares_basis = "filing-cover-fallback"

    period_shares = _clean(period_shares_row["value"]) if period_shares_row else None
    equity = _clean(equity_row["value"]) if equity_row else None
    eps = _clean(eps_row["value"]) if eps_row else None
    bps = equity / period_shares if equity is not None and period_shares and period_shares > 0 else None
    market_cap = price * current_shares if price is not None and current_shares and current_shares > 0 else None
    per = price / eps if price is not None and eps is not None and eps > 0 else None
    pbr = price / bps if price is not None and bps is not None and bps > 0 else None
    result = {"price": price, "market_cap": market_cap, "current_shares_outstanding": current_shares, "current_shares_source": current_shares_basis, "period_end_shares_outstanding": period_shares, "period_end_shares_source": period_shares_row["tag"] if period_shares_row else None, "period_end_shares_basis": period_shares_basis, "eps": eps, "eps_source": eps_row["tag"] if eps_row else None, "eps_basis": eps_basis, "bps": bps, "bps_basis": "parent-attributable-equity-period-end-shares" if bps is not None else None, "bps_equity": equity, "bps_equity_source": equity_row["tag"] if equity_row else None, "per": per, "per_basis": "current-price/latest-full-year-reported-eps" if per is not None else None, "pbr": pbr, "pbr_basis": "current-price/period-end-bps" if pbr is not None else None}
    if equity_row and equity_row.get("basis"):
        result["bps_equity_basis"] = equity_row["basis"]
        result["bps_nci_source_tag"] = equity_row.get("nci_source_tag")
    elif nci_row and equity_row:
        result["bps_nci_note"] = "selected equity fact was not explicitly NCI-inclusive; no subtraction applied"
    if period_shares_row and period_shares_row.get("basis"):
        result["period_end_shares_note"] = period_shares_row["basis"]
        if period_shares_row.get("days_after_fiscal_end") is not None:
            result["period_end_shares_days_after_fiscal_end"] = period_shares_row["days_after_fiscal_end"]
    return result


def normalize_market_quote(info, history=None):
    """Normalize yfinance quote information and one-year history."""
    info = info or {}
    result = {}
    mapping = {"price": ("currentPrice", "regularMarketPrice", "lastPrice"), "volume": ("regularMarketVolume", "volume"), "day_high": ("dayHigh", "regularMarketDayHigh"), "day_low": ("dayLow", "regularMarketDayLow"), "week52_high": ("fiftyTwoWeekHigh", "52WeekHigh"), "week52_low": ("fiftyTwoWeekLow", "52WeekLow"), "current_shares": ("sharesOutstanding", "impliedSharesOutstanding")}
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


def _current_shares_from_sec(companyfacts):
    rows = _dei_share_rows(companyfacts)
    instant_rows = [r for r in rows if not r.get("start")]
    if not instant_rows:
        return None
    latest_end = max(r["end"] for r in instant_rows)
    return _sum_dei_shares_on_date(rows, latest_end)
