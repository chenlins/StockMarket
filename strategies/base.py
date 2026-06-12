import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from data.fetcher import DataFetcher
from data.filters import apply_universe_filters, describe_universe


@dataclass
class StockScore:
    code: str
    name: str
    score: float
    strategy: str
    details: dict


class BaseStrategy(ABC):
    name: str = "base"
    weight: float = 1.0

    def __init__(self, config: dict):
        self.config = config
        self.weight = config.get("weight", 1.0)

    @abstractmethod
    def evaluate(self, code: str, name: str, fetcher: DataFetcher) -> StockScore | None:
        ...


class ScreeningEngine:
    """多策略选股引擎，合并加权得分并输出排名。"""

    def __init__(self, strategies: list[BaseStrategy], config: dict):
        self.strategies = strategies
        self.config = config

    def run(self, fetcher: DataFetcher, stock_list: pd.DataFrame | None = None) -> list[dict]:
        if stock_list is None:
            stock_list = fetcher.get_stock_list()

        screening_cfg = self.config.get("screening", {})
        top_n = screening_cfg.get("top_n", 20)

        total_before = len(stock_list)
        filtered = apply_universe_filters(stock_list, screening_cfg)
        print(f"  分析范围: {describe_universe(screening_cfg)}")
        print(f"  待分析: {len(filtered)} 只 (全市场 {total_before} 只)")

        if filtered.empty:
            print("  警告: 过滤后无符合条件的股票")
            return []

        scores: dict[str, dict] = {}
        total = len(filtered)

        for i, (_, row) in enumerate(filtered.iterrows(), 1):
            code, name = row["code"], row["name"]
            if i % 50 == 0 or i == total:
                print(f"  进度: {i}/{total} ...")

            for strategy in self.strategies:
                result = strategy.evaluate(code, name, fetcher)
                if result is None:
                    continue
                if code not in scores:
                    scores[code] = {
                        "code": code,
                        "name": name,
                        "score": 0.0,
                        "change_pct": _row_change_pct(row),
                        "strategies": [],
                        "details": {},
                    }
                scores[code]["score"] += result.score * strategy.weight
                scores[code]["strategies"].append(result.strategy)
                scores[code]["details"][result.strategy] = result.details

        ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        results = []
        for item in ranked[:top_n]:
            results.append({
                "code": item["code"],
                "name": item["name"],
                "score": round(item["score"], 1),
                "change_pct": item.get("change_pct"),
                "strategy": "+".join(item["strategies"]),
                "details": json.dumps(item["details"], ensure_ascii=False),
            })
        return results


def _row_change_pct(row) -> float | None:
    val = row.get("change_pct") if hasattr(row, "get") else None
    if val is None or pd.isna(val):
        return None
    return round(float(val), 2)
