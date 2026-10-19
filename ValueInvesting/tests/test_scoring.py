from __future__ import annotations

from datetime import date, timedelta

from health.engine import calculate_health


CONFIG = {
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


def history(start: float = 80, daily_step: float = 0.2, count: int = 120) -> list[dict]:
    first_day = date(2025, 1, 1)
    return [
        {
            "date": str(first_day + timedelta(days=index)),
            "close": start + daily_step * index,
            "amount": 350_000_000,
            "turnover": 2.2,
        }
        for index in range(count)
    ]


def test_healthy_company_scores_better_than_risky_company():
    healthy = calculate_health(
        {"name": "示例股份", "price": 100, "pe": 15, "pb": 1.5, "turnover": 2.0},
        {
            "roe": 20,
            "net_margin": 18,
            "revenue_growth": 18,
            "profit_growth": 22,
            "debt_ratio": 35,
            "current_ratio": 1.8,
            "operating_cashflow_per_share": 2.4,
            "eps": 2.0,
        },
        history(),
        CONFIG,
    )
    risky = calculate_health(
        {"name": "*ST示例", "price": 2, "pe": -8, "pb": 9, "turnover": 22},
        {
            "roe": -15,
            "net_margin": -8,
            "revenue_growth": -35,
            "profit_growth": -60,
            "debt_ratio": 88,
            "current_ratio": 0.4,
            "operating_cashflow_per_share": -1.5,
            "eps": -0.8,
        },
        history(100, -0.45),
        CONFIG,
    )

    assert 0 <= risky["score"] < healthy["score"] <= 100
    assert healthy["confidence"]["level"] == "高"
    assert risky["dimensions"][-1]["metrics"][-1]["score"] == 0


def test_missing_fields_use_neutral_score_and_lower_confidence():
    result = calculate_health(
        {"name": "数据缺失样本", "price": 10},
        {},
        [],
        CONFIG,
    )

    assert 0 <= result["score"] <= 100
    assert result["confidence"]["level"] == "低"
    assert result["missing_metrics"]
    missing_metrics = [
        metric
        for dimension in result["dimensions"]
        for metric in dimension["metrics"]
        if metric["status"] == "missing"
    ]
    assert all(metric["score"] == 50 for metric in missing_metrics)


def test_extreme_values_stay_inside_score_boundaries():
    result = calculate_health(
        {"name": "极端样本", "price": 1, "pe": 10000, "pb": 500, "turnover": 999},
        {
            "roe": 1000,
            "net_margin": -1000,
            "revenue_growth": 10000,
            "profit_growth": -10000,
            "debt_ratio": 1000,
            "current_ratio": 1000,
            "operating_cashflow_per_share": -1000,
            "eps": 1,
        },
        history(100, -1, 99),
        CONFIG,
    )

    assert 0 <= result["score"] <= 100
    assert all(
        0 <= metric["score"] <= 100
        for dimension in result["dimensions"]
        for metric in dimension["metrics"]
    )
