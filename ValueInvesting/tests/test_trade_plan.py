from __future__ import annotations

from datetime import date, timedelta

from health.engine import calculate_health
from health.trade_plan import build_trade_plan


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
    rows = []
    for index in range(count):
        close = start + daily_step * index
        rows.append(
            {
                "date": str(first_day + timedelta(days=index)),
                "close": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "amount": 350_000_000,
                "turnover": 2.2,
            }
        )
    return rows


def test_trade_plan_keeps_stop_below_entry_and_target_above():
    result = calculate_health(
        {"name": "示例股份", "price": 100, "pe": 15, "pb": 1.5, "turnover": 2.0},
        {
            "roe": 20,
            "net_margin": 18,
            "revenue_growth": 18,
            "profit_growth": 22,
            "debt_ratio": 35,
            "current_ratio": 1.8,
            "operating_cashflow_per_share": 2.4,
            "eps": 6.5,
        },
        history(),
        CONFIG,
    )
    plan = result["trade_plan"]

    assert plan["available"] is True
    assert plan["stop_loss_price"] < plan["buy_price"] <= plan["price"]
    assert plan["take_profit_price"] > plan["buy_price"]
    assert plan["reward_risk"] >= 1.5
    assert plan["buy_analysis"]
    assert plan["take_profit_analysis"]
    assert plan["stop_loss_analysis"]
    assert "元" in plan["summary"]


def test_risky_or_st_stock_is_not_a_buy_recommendation():
    result = calculate_health(
        {"name": "*ST示例", "price": 8, "pe": 80, "pb": 9, "turnover": 22},
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
        history(12, -0.03),
        CONFIG,
    )
    plan = result["trade_plan"]

    assert plan["recommended"] is False
    assert plan["action_code"] == "avoid"
    assert plan["stop_loss_price"] < plan["buy_price"] < plan["take_profit_price"]
    assert plan["batches"] == []


def test_cheap_average_score_is_a_scaled_buy():
    plan = build_trade_plan(
        {"name": "粤桂股份", "price": 20.65, "pe": 13.32, "pb": 3.3},
        {"roe": 15.8, "eps": 1.55},
        history(18, 0.02),
        {"volatility": 28},
        69.6,
        "一般",
    )

    assert plan["recommended"] is True
    assert plan["action_code"] == "buy"
    assert plan["action"] == "建议分批买入"
    assert "不构成买入建议" not in plan["summary"]


def test_rich_average_score_still_waits():
    plan = build_trade_plan(
        {"name": "偏贵股份", "price": 20, "pe": 17.9, "pb": 2.4},
        {"roe": 15, "eps": 1.1},
        history(18, 0.02),
        {"volatility": 28},
        69.6,
        "一般",
    )

    assert plan["recommended"] is False
    assert plan["action_code"] == "wait"


def test_extended_healthy_price_waits_for_ma20():
    closes = history(50, 0, 120)
    plan = build_trade_plan(
        {"name": "趋势股份", "price": 60, "pe": 12, "pb": 1.6},
        {"roe": 22, "eps": 4},
        closes,
        {"volatility": 25},
        88,
        "优秀",
    )

    assert plan["action_code"] == "pullback"
    assert plan["recommended"] is True
    assert plan["buy_price"] == 50
    assert plan["take_profit_price"] > 60
    assert "20 日均线" in plan["buy_analysis"]


def test_saved_report_without_a_plan_gets_one_back():
    from health.trade_plan import ensure_trade_plan

    payload = {
        "score": 72,
        "grade": "良好",
        "stock": {"name": "测试股份", "price": 20, "pe": 16},
        "raw_metrics": {"financials": {"roe": 15, "eps": 1.2}},
        "calculated_metrics": {"volatility": 25},
    }

    assert ensure_trade_plan(payload) is True
    assert payload["trade_plan"]["available"] is True
    assert payload["trade_plan"]["stop_loss_price"] < payload["trade_plan"]["buy_price"]
    assert ensure_trade_plan(payload) is False


def test_missing_price_does_not_invent_levels():
    plan = build_trade_plan({"name": "无价格"}, {}, [], {}, 60, "一般")

    assert plan["available"] is False
    assert plan["buy_price"] is None
    assert plan["take_profit_price"] is None
    assert plan["stop_loss_price"] is None
