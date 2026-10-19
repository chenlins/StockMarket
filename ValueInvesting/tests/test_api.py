from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from api.app import create_app
from data.fetcher import DataResult, StockNotFoundError


CONFIG = {
    "data": {"db_path": ":memory:"},
    "scoring": {
        "weights": {
            "profitability": 30,
            "valuation": 20,
            "safety": 20,
            "trend": 20,
            "liquidity": 10,
        },
        "grades": {"excellent": 85, "good": 70, "average": 55, "weak": 40},
    },
}


class FakeFetcher:
    def get_quote(self, code):
        if code == "600000":
            raise StockNotFoundError("股票不存在")
        return DataResult(
            {
                "code": code,
                "name": "测试股份",
                "price": 20,
                "change_pct": 1.2,
                "amount": 200_000_000,
                "turnover": 2,
                "pe": 16,
                "pb": 1.7,
                "market_cap": 20_000_000_000,
            },
            "测试行情",
            "2026-01-01T00:00:00+00:00",
        )

    def get_financials(self, code):
        return DataResult(
            {
                "report_date": "2025-12-31",
                "roe": 17,
                "net_margin": 12,
                "revenue_growth": 11,
                "profit_growth": 15,
                "debt_ratio": 42,
                "current_ratio": 1.6,
                "operating_cashflow_per_share": 1.5,
                "eps": 1.2,
            },
            "测试财务",
            "2026-01-01T00:00:00+00:00",
        )

    def get_history(self, code, days):
        start = date(2025, 1, 1)
        rows = [
            {
                "date": str(start + timedelta(days=index)),
                "close": 10 + index * 0.05,
                "amount": 180_000_000,
                "turnover": 1.8,
            }
            for index in range(days)
        ]
        return DataResult(rows, "测试日线", "2026-01-01T00:00:00+00:00")


client = TestClient(create_app(CONFIG, FakeFetcher()))


def test_health_endpoint_returns_explainable_report():
    response = client.get("/api/health/600519")
    assert response.status_code == 200
    body = response.json()
    assert body["stock"]["code"] == "600519"
    assert 0 <= body["score"] <= 100
    assert len(body["dimensions"]) == 5
    assert body["summary"]["overview"]
    assert body["trade_plan"]["available"] is True
    assert body["trade_plan"]["stop_loss_price"] < body["trade_plan"]["buy_price"]
    assert body["trade_plan"]["take_profit_price"] > body["trade_plan"]["buy_price"]
    assert body["data_sources"]["financials"]["source"] == "测试财务"


def test_buy_section_stays_on_the_page_and_is_not_cached_away():
    page = client.get("/")
    script = client.get("/app.js")
    stylesheet = client.get("/styles.css")

    assert page.status_code == 200
    assert 'id="buySection"' in page.text
    assert 'id="tradePlan"' in page.text
    assert "推荐买入" in page.text
    assert "report-pending" in page.text
    assert "no-cache" in page.headers.get("cache-control", "")
    assert "function renderTradePlan" in script.text
    assert 'querySelector("#tradePlan")' in script.text
    assert "value-investing.last-report.v1" in script.text
    assert "no-cache" in script.headers.get("cache-control", "")
    assert "#buySection" in stylesheet.text


def test_invalid_code_returns_400():
    response = client.get("/api/health/123")
    assert response.status_code == 400
    assert "6 位" in response.json()["detail"]


def test_unknown_stock_returns_404():
    response = client.get("/api/health/600000")
    assert response.status_code == 404


def test_health_endpoint_falls_back_to_market_financials():
    class FallbackFetcher(FakeFetcher):
        def get_financials(self, code):
            raise RuntimeError("单股财务接口失败")

        def get_market_financials(self):
            return DataResult(
                {
                    "stocks": {
                        "600519": {
                            "report_date": "2025-12-31",
                            "roe": 19,
                            "net_margin": 14,
                            "revenue_growth": 12,
                            "profit_growth": 16,
                            "operating_cashflow_per_share": 1.8,
                            "eps": 1.2,
                        }
                    }
                },
                "测试批量财务",
                "2026-01-01T00:00:00+00:00",
            )

    fallback_client = TestClient(create_app(CONFIG, FallbackFetcher()))
    response = fallback_client.get("/api/health/600519")
    assert response.status_code == 200
    body = response.json()
    assert body["raw_metrics"]["financials"]["roe"] == 19
    assert "批量财务回退" in body["data_sources"]["financials"]["source"]
    assert any("批量财务数据回退" in warning for warning in body["warnings"])


def test_ranking_snapshot_endpoint_returns_stored_report_unchanged():
    app = create_app(CONFIG, FakeFetcher())
    expected = {"stock": {"code": "600519"}, "score": 74.1, "dimensions": [{"name": "测试"}]}

    class SnapshotScreener:
        def report(self, code):
            return expected if code == "600519" else None

    app.state.screener = SnapshotScreener()
    snapshot_client = TestClient(app)

    response = snapshot_client.get("/api/top-health/600519")
    assert response.status_code == 200
    assert response.json() == expected
    assert snapshot_client.get("/api/top-health/000001").status_code == 404
