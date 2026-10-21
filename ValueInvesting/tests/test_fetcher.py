from datetime import datetime, timedelta, timezone

from data.fetcher import DataFetchError, DataResult, StockDataFetcher, _parse_tencent_quote, resolve_scoring_financials
from data.storage import CacheStorage


class FinancialFetcher:
    def __init__(self, individual: dict, market: dict):
        self.individual = individual
        self.market = market
        self.market_calls = 0

    def get_financials(self, code):
        return DataResult(self.individual, "单股财务", "2026-01-02T00:00:00+00:00")

    def get_market_financials(self):
        self.market_calls += 1
        return DataResult(
            {"stocks": {"600001": self.market}},
            "批量财务",
            "2026-01-01T00:00:00+00:00",
        )


def test_financial_resolver_supplements_only_the_same_report_period():
    individual = {
        "report_date": "2025-12-31",
        "roe": 18,
        "net_margin": 12,
        "revenue_growth": 10,
        "profit_growth": 11,
        "debt_ratio": None,
        "current_ratio": 1.5,
        "operating_cashflow_per_share": 1.2,
        "eps": 1,
    }
    market = {
        "report_date": "2025-12-31",
        "roe": 99,
        "debt_ratio": 42,
        "annualized_eps": 2,
    }
    fetcher = FinancialFetcher(individual, market)

    result = resolve_scoring_financials(fetcher, "600001")

    assert fetcher.market_calls == 1
    assert result.data["roe"] == 18
    assert result.data["debt_ratio"] == 42
    assert result.data["annualized_eps"] == 2
    assert "同报告期" in result.source


def test_financial_resolver_does_not_mix_different_report_periods():
    individual = {
        "report_date": "2025-12-31",
        "roe": 18,
        "net_margin": 12,
        "revenue_growth": 10,
        "profit_growth": 11,
        "debt_ratio": None,
        "current_ratio": 1.5,
        "operating_cashflow_per_share": 1.2,
        "eps": 1,
    }
    market = {
        "report_date": "2025-09-30",
        "debt_ratio": 20,
        "annualized_eps": 3,
    }

    result = resolve_scoring_financials(FinancialFetcher(individual, market), "600001")

    assert result.data["report_date"] == "2025-12-31"
    assert result.data["debt_ratio"] is None
    assert "annualized_eps" not in result.data
    assert "报告期不同" in result.source


def test_parse_tencent_quote_uses_yuan_for_amount_and_market_cap():
    parts = ["v"] * 53
    parts[1] = "贵州茅台"
    parts[2] = "600519"
    parts[3] = "1345.00"
    parts[32] = "1.20"
    parts[37] = "243854"
    parts[38] = "0.15"
    parts[39] = "20.65"
    parts[52] = "18.84"
    parts[45] = "16813.60"
    parts[46] = "6.69"
    parts[49] = "2.01"
    quote = _parse_tencent_quote("600519", "~".join(parts))

    assert quote["name"] == "贵州茅台"
    assert quote["price"] == 1345
    assert quote["amount"] == 243854 * 10000
    assert quote["market_cap"] == 16813.60 * 100_000_000
    assert quote["pe"] == 18.84
    assert quote["pb"] == 6.69


class _HotRankResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._rows}


def test_hot_rank_pages_popularity_list_until_limit(monkeypatch, tmp_path):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json["pageNo"]))
        if "getAllCurrentList" not in url:
            return _HotRankResponse([])
        start = (json["pageNo"] - 1) * 100
        rows = [
            {"sc": f"SH{600000 + start + offset:06d}", "rk": start + offset + 1}
            for offset in range(100)
        ]
        return _HotRankResponse(rows)

    monkeypatch.setattr("data.fetcher.requests.post", fake_post)
    fetcher = StockDataFetcher(
        CacheStorage(tmp_path / "cache.db"),
        {"retries": 1, "request_interval_seconds": 0, "hot_rank_cache_minutes": 30},
    )

    result = fetcher.get_hot_rank(250)

    assert len(result.data) == 250
    assert result.data[0]["code"] == "600000"
    assert result.data[249]["code"] == "600249"
    assert result.data[249]["heat_rank"] == 250
    assert result.data[249]["heat_list"] == "人气榜"
    assert [page for url, page in calls if "getAllCurrentList" in url] == [1, 2, 3]
    assert not any("getAllHisRcList" in url for url, _page in calls)


def test_hot_rank_fills_from_rising_list_when_popularity_is_short(monkeypatch, tmp_path):
    def fake_post(url, json, timeout):
        if "getAllCurrentList" in url:
            if json["pageNo"] > 1:
                return _HotRankResponse([])
            return _HotRankResponse(
                [{"sc": "SH600001", "rk": 1}, {"sc": "SH600002", "rk": 2}]
            )
        return _HotRankResponse(
            [
                {"sc": "SH600002", "hrcrk": 1},
                {"sc": "SZ000001", "hrcrk": 2},
                {"sc": "SZ300001", "hrcrk": 3},
            ]
        )

    monkeypatch.setattr("data.fetcher.requests.post", fake_post)
    fetcher = StockDataFetcher(
        CacheStorage(tmp_path / "cache.db"),
        {"retries": 1, "request_interval_seconds": 0, "hot_rank_cache_minutes": 30},
    )

    result = fetcher.get_hot_rank(4)

    assert [item["code"] for item in result.data] == ["600001", "600002", "000001", "300001"]
    assert [item["heat_list"] for item in result.data] == ["人气榜", "人气榜", "飙升榜", "飙升榜"]


def test_history_live_source_wins_over_older_cached_source(monkeypatch, tmp_path):
    storage = CacheStorage(tmp_path / "cache.db")
    storage.put("history:600519:120:eastmoney", [{"date": "2026-10-14", "close": 1}], "东方财富前复权日线")
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with storage._connect() as connection:
        connection.execute(
            "UPDATE cache SET fetched_at = ? WHERE cache_key = ?",
            (old, "history:600519:120:eastmoney"),
        )
    monkeypatch.setattr(
        "data.fetcher._fetch_tencent_history",
        lambda code, days: [{"date": "2026-10-19", "close": 1345, "amount": 2_000_000_000, "turnover": 0.2}],
    )
    fetcher = StockDataFetcher(
        storage,
        {"retries": 1, "request_interval_seconds": 0, "allow_stale_cache_days": 14, "history_cache_hours": 12},
    )

    result = fetcher.get_history("600519", 120)

    assert result.stale is False
    assert result.data[-1]["date"] == "2026-10-19"
    assert result.data[-1]["close"] == 1345
    assert "腾讯" in result.source


def test_history_uses_newest_cache_only_after_every_live_source_fails(monkeypatch, tmp_path):
    storage = CacheStorage(tmp_path / "cache.db")
    storage.put("history:600519:120:eastmoney", [{"date": "2026-10-14", "close": 9}], "东方财富前复权日线")
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    with storage._connect() as connection:
        connection.execute(
            "UPDATE cache SET fetched_at = ? WHERE cache_key = ?",
            (old, "history:600519:120:eastmoney"),
        )

    def fail(*args, **kwargs):
        raise DataFetchError("down")

    monkeypatch.setattr("data.fetcher._fetch_tencent_history", fail)
    monkeypatch.setattr("data.fetcher._fetch_eastmoney_history", fail)
    monkeypatch.setattr("data.fetcher.ak.stock_zh_a_daily", fail)
    fetcher = StockDataFetcher(
        storage,
        {"retries": 1, "request_interval_seconds": 0, "allow_stale_cache_days": 14, "history_cache_hours": 12},
    )

    result = fetcher.get_history("600519", 120)

    assert result.stale is True
    assert result.data[-1]["date"] == "2026-10-14"
    assert "东方财富" in result.source
