"""US market downturn-defense calculator.

Calculates a structural 0~20-ish defense value without storing price history in Supabase.
Benchmark: S&P 500 (^GSPC). Market-defined bear episodes are detected dynamically.
"""

from datetime import datetime
import math

import pandas as pd
import yfinance as yf

BENCHMARK = "^GSPC"
START_DATE = "2019-01-01"
BEAR_THRESHOLD = -0.20
RECOVERY_THRESHOLD = -0.05
RECOVERY_TARGET = 0.90


def _close_series(ticker, start=START_DATE):
    df = yf.download(
        ticker,
        start=start,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return None
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = pd.to_numeric(close, errors="coerce").dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


def _bear_episodes(market):
    """Detect S&P 500 bear-market episodes from running drawdown.

    Start = first <= -20% drawdown from a prior peak.
    End   = first recovery above -5% drawdown after entering the bear market.
    This prevents the 2022 bear market's temporary rallies from splitting it.
    """
    if market is None or len(market) < 30:
        return []

    peak = market.cummax()
    dd = market / peak - 1.0
    in_bear = False
    start = None
    episodes = []

    for date, value in dd.items():
        if not in_bear and value <= BEAR_THRESHOLD:
            in_bear = True
            start = date
        elif in_bear and value >= RECOVERY_THRESHOLD:
            end = date
            if start is not None and (end - start).days >= 20:
                episodes.append((start, end))
            in_bear = False
            start = None

    if in_bear and start is not None:
        episodes.append((start, market.index[-1]))

    return episodes


def _max_drawdown(series):
    if series is None or len(series) < 2:
        return None, None
    peak = series.cummax()
    dd = series / peak - 1.0
    trough_date = dd.idxmin()
    return float(dd.min() * 100.0), trough_date


def _recovery_days(series, trough_date):
    if series is None or trough_date is None:
        return None
    pre = series.loc[:trough_date]
    if pre.empty:
        return None
    peak = float(pre.cummax().iloc[-1])
    trough = float(series.loc[trough_date])
    if peak <= 0:
        return None
    target = trough + (peak - trough) * RECOVERY_TARGET
    after = series.loc[trough_date:]
    recovered = after[after >= target]
    if recovered.empty:
        return None
    return int((recovered.index[0] - trough_date).days)


def calculate_downturn_defense(ticker, market=None, stock=None):
    """Return (defense_value, detail).

    Defense value combines:
      70% relative maximum-drawdown advantage
      30% recovery-speed advantage
    Both components are expressed in percentage-point units so the final value
    can be passed directly to scoring.py's downturn_defense bands.
    """
    if market is None:
        market = _close_series(BENCHMARK)
    if stock is None:
        stock = _close_series(ticker)
    if market is None or stock is None:
        return None, {"status": "price_data_unavailable", "episodes": []}

    episodes = _bear_episodes(market)
    results = []

    for start, end in episodes:
        m = market.loc[start:end]
        s = stock.loc[start:end].dropna()
        if len(m) < 10 or len(s) < 10:
            continue
        m_dd, m_trough = _max_drawdown(m)
        s_dd, s_trough = _max_drawdown(s)
        if m_dd is None or s_dd is None:
            continue

        relative_advantage = s_dd - m_dd
        m_recovery = _recovery_days(m, m_trough)
        s_recovery = _recovery_days(s, s_trough)
        if m_recovery and m_recovery > 0 and s_recovery is not None:
            recovery_advantage = max(-20.0, min(20.0, (m_recovery - s_recovery) / m_recovery * 20.0))
        else:
            recovery_advantage = 0.0

        combined = 0.70 * relative_advantage + 0.30 * recovery_advantage
        results.append({
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "market_drawdown": round(m_dd, 2),
            "stock_drawdown": round(s_dd, 2),
            "relative_advantage": round(relative_advantage, 2),
            "market_recovery_days": m_recovery,
            "stock_recovery_days": s_recovery,
            "recovery_advantage": round(recovery_advantage, 2),
            "combined": round(combined, 2),
        })

    if not results:
        return None, {"status": "no_bear_episode", "episodes": []}

    value = sum(x["combined"] for x in results) / len(results)
    value = max(-50.0, min(30.0, value))
    return round(value, 2), {
        "status": "ok",
        "episodes": results,
        "episodes_used": len(results),
    }
