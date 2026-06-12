import numpy as np
import pandas as pd


def add_ma(df: pd.DataFrame, windows: list[int] | None = None) -> pd.DataFrame:
    windows = windows or [5, 10, 20, 60]
    result = df.copy()
    for w in windows:
        result[f"ma{w}"] = result["close"].rolling(w, min_periods=w).mean()
    return result


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    result = df.copy()
    delta = result["close"].diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    result[f"rsi{period}"] = 100 - (100 / (1 + rs))
    return result


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    result = df.copy()
    ema_fast = result["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = result["close"].ewm(span=slow, adjust=False).mean()
    result["macd"] = ema_fast - ema_slow
    result["macd_signal"] = result["macd"].ewm(span=signal, adjust=False).mean()
    result["macd_hist"] = result["macd"] - result["macd_signal"]
    return result


def calc_volume_ratio(df: pd.DataFrame, window: int = 5) -> float:
    """最新成交量 / N 日均量。"""
    if len(df) < window + 1:
        return 0.0
    avg_vol = df["volume"].iloc[-(window + 1):-1].mean()
    if avg_vol == 0:
        return 0.0
    return float(df["volume"].iloc[-1] / avg_vol)
