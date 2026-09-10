"""US market downturn-defense calculator.

Calculates a structural defense value without storing price history in Supabase.
Benchmark: S&P 500 (^GSPC). Market-defined bear episodes are detected dynamically.
"""

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
    if close.empty:
        return None
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close


def _bear_episodes(market):
    if market is None or len(market) < 30:
        return []
    peak_values = market.cummax()
    dd = market / peak_values - 1.0
    in_bear = False
    bear_start = None
    episodes = []
    for date, value in dd.items():
        if not in_bear and value <= BEAR_THRESHOLD:
            prior = market.loc[:date]
            peak_value = prior.max()
            peak_candidates = prior[prior == peak_value]
            bear_start = peak_candidates.index[-1]
            in_bear = True
        elif in_bear and value >= RECOVERY_THRESHOLD:
            end = date
            if bear_start is not None and (end - bear_start).days >= 20:
                episodes.append((bear_start, end))
            in_bear = False
            bear_start = None
    if in_bear and bear_start is not None:
        episodes.append((bear_start, market.index[-1]))
    return episodes


def _max_drawdown(series):
    if series is None or len(series) < 2:
        return None, None
    series = pd.to_numeric(series, errors="coerce").dropna()
    if len(series) < 2:
        return None, None
    peak = series.cummax()
    dd = (series / peak - 1.0).dropna()
    if dd.empty:
        return None, None
    trough_date = dd.idxmin()
    if pd.isna(trough_date):
        return None, None
    return float(dd.min() * 100.0), trough_date


def _recovery_days(series, trough_date, recovery_end=None):
    if series is None or trough_date is None or pd.isna(trough_date):
        return None
    series = pd.to_numeric(series, errors="coerce").dropna()
    if series.empty or trough_date not in series.index:
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
    if recovery_end is not None:
        after = after.loc[:recovery_end]
    recovered = after[after >= target]
    if recovered.empty:
        return None
    return int((recovered.index[0] - trough_date).days)


def calculate_downturn_defense(ticker, market=None, stock=None):
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
        m_recovery = _recovery_days(market, m_trough)
        s_recovery = _recovery_days(stock, s_trough)
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
    return round(value, 2), {"status": "ok", "episodes": results, "episodes_used": len(results)}
