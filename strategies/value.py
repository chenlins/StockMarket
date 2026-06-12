import pandas as pd

from indicators.fundamental import score_value_metrics
from data.fetcher import DataFetcher
from .base import BaseStrategy, StockScore


def _to_float(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    try:
        v = float(val)
        return v if pd.notna(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


class ValueStrategy(BaseStrategy):
    """价值投资策略：低 PE/PB + 高 ROE。"""

    name = "value"

    def evaluate(self, code: str, name: str, fetcher: DataFetcher) -> StockScore | None:
        financial = fetcher.get_financial_indicators(code)
        if financial is None:
            spot = fetcher.get_stock_list()
            row = spot[spot["code"] == code]
            if row.empty:
                return None
            financial = {
                "pe": _to_float(row.iloc[0].get("pe")),
                "pb": _to_float(row.iloc[0].get("pb")),
                "roe": 0.0,
            }
            if financial["pe"] <= 0:
                return None

        pe, pb, roe = financial["pe"], financial["pb"], financial["roe"]
        pe_max = self.config.get("pe_max", 25)
        pb_max = self.config.get("pb_max", 3)
        roe_min = self.config.get("roe_min", 10)

        if pe <= 0 or pe > pe_max:
            return None
        if pb > pb_max:
            return None

        score, details = score_value_metrics(pe, pb, roe, pe_max, pb_max, roe_min)
        if score < 30:
            return None

        return StockScore(
            code=code,
            name=name,
            score=score,
            strategy=self.name,
            details=details,
        )
