"""Transparent baseline signal using only completed bars."""
from __future__ import annotations

import pandas as pd


def calculate_signal(rates) -> dict:
    """Input is MT5 rates with time/open/high/low/close/tick_volume fields.

    Signal is informational. It does not place orders.
    """
    df = pd.DataFrame(rates)
    if len(df) < 60:
        return {"signal": "WAIT", "reason": "Not enough completed bars"}

    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.where(loss != 0)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(100)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    last = len(df) - 1
    vals = (ema20.iloc[last], ema50.iloc[last], rsi.iloc[last], atr.iloc[last])
    if any(pd.isna(v) for v in vals) or atr.iloc[last] <= 0:
        return {"signal": "WAIT", "reason": "Indicators not ready"}

    signal = "WAIT"
    if ema20.iloc[last] > ema50.iloc[last] and 50 < rsi.iloc[last] < 68:
        signal = "BUY"
    elif ema20.iloc[last] < ema50.iloc[last] and 32 < rsi.iloc[last] < 50:
        signal = "SELL"

    return {
        "signal": signal,
        "reason": "EMA20/EMA50 trend with RSI14 confirmation",
        "close": float(close.iloc[last]),
        "ema20": float(ema20.iloc[last]),
        "ema50": float(ema50.iloc[last]),
        "rsi14": float(rsi.iloc[last]),
        "atr14": float(atr.iloc[last]),
        "bar_time": int(df["time"].iloc[last]),
    }
