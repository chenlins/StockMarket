import pandas as pd

from indicators.technical import add_ma, calc_volume_ratio
from data.fetcher import DataFetcher
from .base import BaseStrategy, StockScore


class MomentumStrategy(BaseStrategy):
    """动量策略：均线多头排列 + 放量。"""

    name = "momentum"

    def evaluate(self, code: str, name: str, fetcher: DataFetcher) -> StockScore | None:
        history = fetcher.get_daily_history(code, days=60)
        if history.empty or len(history) < 25:
            return None

        ma_short = self.config.get("ma_short", 5)
        ma_long = self.config.get("ma_long", 20)
        vol_ratio_min = self.config.get("volume_ratio_min", 1.2)

        df = add_ma(history, [ma_short, ma_long])
        latest = df.iloc[-1]
        close = latest["close"]
        ma_s = latest[f"ma{ma_short}"]
        ma_l = latest[f"ma{ma_long}"]

        if any(pd.isna(v) for v in [close, ma_s, ma_l]):
            return None

        vol_ratio = calc_volume_ratio(df, window=5)
        details = {
            "close": round(close, 2),
            f"ma{ma_short}": round(ma_s, 2),
            f"ma{ma_long}": round(ma_l, 2),
            "volume_ratio": round(vol_ratio, 2),
        }

        score = 0.0
        if close > ma_s:
            score += 30
            details["above_ma_short"] = True
        if close > ma_l:
            score += 30
            details["above_ma_long"] = True
        if ma_s > ma_l:
            score += 20
            details["ma_bullish"] = True
        if vol_ratio >= vol_ratio_min:
            score += 20
            details["volume_surge"] = True

        if score < 50:
            return None

        return StockScore(
            code=code,
            name=name,
            score=score,
            strategy=self.name,
            details=details,
        )
