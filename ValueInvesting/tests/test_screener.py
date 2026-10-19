from __future__ import annotations

from datetime import date, timedelta

from data.fetcher import DataResult
from health.screener import (
    TopHealthScreener,
    _enrich_quote,
    _is_eligible,
    _select_reliable_first,
)


ROOT_CONFIG = {
    "scoring": {
        "weights": {
            "profitability": 30,
            "valuation": 20,
            "safety": 20,
            "trend": 20,
            "liquidity": 10,
        },
        "grades": {"excellent": 85, "good": 70, "average": 55, "weak": 40},
    }
}


class MemoryStorage:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def put(self, key, payload, source):
        self.values[key] = {"payload": payload, "source": source}


class RankingFetcher:
    def get_quote_list(self):
        quotes = [
            {
                "code": f"600{index:03d}",
                "name": f"样本{index}",
                "price": 10 + index,
                "pe": 12 + index,
                "pb": 1.1 + index * 0.1,
                "amount": 300_000_000,
                "turnover": 2.5,
                "market_cap": 30_000_000_000,
                "change_pct": 0.5,
            }
            for index in range(12)
        ]
        return DataResult(quotes, "测试行情", "2026-01-01T00:00:00+00:00")

    def get_hot_rank(self, limit):
        return DataResult(
            [
                {
                    "code": f"600{index:03d}",
                    "heat_rank": index + 1,
                    "source_rank": index + 1,
                    "heat_list": "测试人气榜",
                }
                for index in range(min(limit, 12))
            ],
            "测试热度榜",
            "",
        )

    def get_market_financials(self):
        stocks = {}
        for index in range(12):
            stocks[f"600{index:03d}"] = {
                "report_date": "2025-12-31",
                "roe": 22 - index,
                "net_margin": 18 - index / 2,
                "revenue_growth": 20 - index,
                "profit_growth": 22 - index,
                "debt_ratio": 35 + index,
                "current_ratio": 1.8,
                "operating_cashflow_per_share": 2,
                "eps": 1.5,
                "annualized_eps": 1.5,
                "book_value_per_share": 10,
            }
        return DataResult(
            {"report_date": "2025-12-31", "stocks": stocks},
            "测试批量财务",
            "",
        )

    def get_financials(self, code):
        index = int(code[-3:])
        return DataResult(
            {
                "roe": 22 - index,
                "net_margin": 18 - index / 2,
                "revenue_growth": 20 - index,
                "profit_growth": 22 - index,
                "debt_ratio": 35 + index,
                "current_ratio": 1.8,
                "operating_cashflow_per_share": 2,
                "eps": 1.5,
            },
            "测试财务",
            "",
        )

    def get_history(self, code, days):
        first = date(2025, 1, 1)
        rows = [
            {
                "date": str(first + timedelta(days=index)),
                "close": 10 + index * 0.05,
                "amount": 300_000_000,
                "turnover": 2.5,
            }
            for index in range(days)
        ]
        return DataResult(rows, "测试日线", "")


def test_two_stage_screener_returns_clickable_top_ten_payload():
    storage = MemoryStorage()
    screener = TopHealthScreener(
        RankingFetcher(),
        storage,
        {
            "enabled": True,
            "top_count": 10,
            "hot_limit": 12,
            "workers": 2,
            "root_config": ROOT_CONFIG,
        },
    )

    screener._refresh()
    state = screener.snapshot()

    assert state["status"] == "ready"
    assert len(state["results"]) == 10
    assert state["results"] == sorted(state["results"], key=lambda item: item["score"], reverse=True)
    assert all(item["code"].startswith("600") for item in state["results"])
    assert all(item["heat_rank"] is not None for item in state["results"])
    assert state["heat_source"] == "测试热度榜"
    assert "reports" not in state
    for item in state["results"]:
        detail = screener.report(item["code"])
        assert detail is not None
        assert detail["score"] == item["score"]
        assert detail["dimensions"]
        assert detail["snapshot"]["kind"] == "ranking"
        assert detail["snapshot"]["score"] == item["score"]
        assert detail["trade_plan"]["buy_price"]
    assert storage.get(TopHealthScreener.CACHE_KEY) is not None


def test_ranking_keeps_reliable_stocks_before_low_confidence_fillers():
    ordered = [
        {
            "compact": {
                "code": f"600{index:03d}",
                "score": 100 - index,
                "confidence": {"score": 40 if index < 4 else 80},
            }
        }
        for index in range(8)
    ]

    selected = _select_reliable_first(ordered, 6, 60)

    assert [item["compact"]["code"] for item in selected[:4]] == [
        "600004",
        "600005",
        "600006",
        "600007",
    ]
    assert [item["compact"]["code"] for item in selected[4:]] == ["600000", "600001"]


def test_financially_derived_valuation_is_rechecked_for_eligibility():
    quote = {
        "code": "600001",
        "name": "高估值样本",
        "price": 100,
        "amount": 300_000_000,
        "pe": None,
        "pb": None,
    }
    enriched = _enrich_quote(
        quote,
        {"annualized_eps": 1, "book_value_per_share": 5},
    )

    assert enriched["pe"] == 100
    assert enriched["pb"] == 20
    assert not _is_eligible(enriched)
