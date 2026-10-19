from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .explain import build_narrative
from .trade_plan import build_trade_plan


@dataclass
class MetricSpec:
    key: str
    label: str
    value: float | None
    unit: str
    weight: float
    scorer: Callable[[float], float]
    good_text: str
    bad_text: str


def _piecewise(value: float, points: list[tuple[float, float]]) -> float:
    ordered = sorted(points)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x1, y1), (x2, y2) in zip(ordered, ordered[1:]):
        if x1 <= value <= x2:
            ratio = (value - x1) / (x2 - x1)
            return y1 + ratio * (y2 - y1)
    return 50.0


def _band(value: float, points: list[tuple[float, float]]) -> float:
    return max(0.0, min(100.0, _piecewise(value, points)))


def _metric_result(spec: MetricSpec) -> dict:
    if spec.value is None or not math.isfinite(spec.value):
        return {
            "key": spec.key,
            "label": spec.label,
            "value": None,
            "unit": spec.unit,
            "score": 50.0,
            "weight": spec.weight,
            "status": "missing",
            "explanation": f"{spec.label}数据缺失，按中性分处理，已降低可信度。",
        }
    score = round(max(0.0, min(100.0, spec.scorer(spec.value))), 1)
    status = "positive" if score >= 70 else "negative" if score < 45 else "neutral"
    if status == "positive":
        text = spec.good_text
    elif status == "negative":
        text = spec.bad_text
    else:
        text = "处于中间区间，对健康分影响有限。"
    return {
        "key": spec.key,
        "label": spec.label,
        "value": round(spec.value, 2),
        "unit": spec.unit,
        "score": score,
        "weight": spec.weight,
        "status": status,
        "explanation": text,
    }


def _dimension(key: str, name: str, max_score: float, specs: list[MetricSpec]) -> dict:
    metrics = [_metric_result(spec) for spec in specs]
    denominator = sum(metric["weight"] for metric in metrics) or 1
    percentage = sum(metric["score"] * metric["weight"] for metric in metrics) / denominator
    score = round(percentage * max_score / 100, 1)
    positives = [
        f'{metric["label"]}：{metric["explanation"]}'
        for metric in metrics
        if metric["status"] == "positive"
    ]
    risks = [
        f'{metric["label"]}：{metric["explanation"]}'
        for metric in metrics
        if metric["status"] in ("negative", "missing")
    ]
    return {
        "key": key,
        "name": name,
        "score": score,
        "max_score": max_score,
        "percentage": round(percentage, 1),
        "metrics": metrics,
        "positives": positives,
        "risks": risks,
    }


def _history_metrics(history: list[dict]) -> dict:
    closes = np.array(
        [float(row["close"]) for row in history if row.get("close") not in (None, "")],
        dtype=float,
    )
    closes = closes[np.isfinite(closes)]
    if len(closes) < 20:
        return {
            "ma20_gap": None,
            "ma60_gap": None,
            "return_120": None,
            "max_drawdown": None,
            "volatility": None,
            "avg_amount": None,
            "avg_turnover": None,
        }

    latest = closes[-1]
    ma20 = float(np.mean(closes[-20:]))
    ma60 = float(np.mean(closes[-60:])) if len(closes) >= 60 else None
    running_max = np.maximum.accumulate(closes)
    drawdowns = closes / running_max - 1
    daily_returns = np.diff(closes) / closes[:-1]
    amounts = [_finite(row.get("amount")) for row in history[-20:]]
    turnovers = [_finite(row.get("turnover")) for row in history[-20:]]
    return {
        "ma20_gap": (latest / ma20 - 1) * 100 if ma20 else None,
        "ma60_gap": (latest / ma60 - 1) * 100 if ma60 else None,
        "return_120": (latest / closes[0] - 1) * 100 if closes[0] else None,
        "max_drawdown": float(np.min(drawdowns) * 100),
        "volatility": float(np.std(daily_returns) * np.sqrt(250) * 100) if len(daily_returns) else None,
        "avg_amount": _mean_present(amounts),
        "avg_turnover": _mean_present(turnovers),
    }


def _cash_quality(financials: dict) -> float | None:
    cashflow = _finite(financials.get("operating_cashflow_per_share"))
    eps = _finite(financials.get("eps"))
    if cashflow is None:
        return None
    if eps is not None and eps > 0:
        return cashflow / eps * 100
    return 100.0 if cashflow > 0 else 0.0


def calculate_health(
    quote: dict,
    financials: dict,
    history: list[dict],
    config: dict,
) -> dict:
    weights = config["scoring"]["weights"]
    technical = _history_metrics(history)

    profitability = _dimension(
        "profitability",
        "盈利与成长",
        weights["profitability"],
        [
            MetricSpec("roe", "净资产收益率", _finite(financials.get("roe")), "%", 30, lambda v: _band(v, [(-10, 0), (0, 25), (8, 55), (15, 82), (25, 100)]), "股东资本回报能力较强。", "资本回报偏低或为负。"),
            MetricSpec("net_margin", "销售净利率", _finite(financials.get("net_margin")), "%", 20, lambda v: _band(v, [(-10, 0), (0, 25), (5, 55), (15, 82), (30, 100)]), "主营业务盈利空间较好。", "盈利空间较薄或处于亏损。"),
            MetricSpec("revenue_growth", "营收增长率", _finite(financials.get("revenue_growth")), "%", 20, _growth_score, "收入保持健康增长。", "收入出现明显收缩。"),
            MetricSpec("profit_growth", "净利润增长率", _finite(financials.get("profit_growth")), "%", 20, _growth_score, "利润增长较有活力。", "利润下滑对经营健康构成压力。"),
            MetricSpec("cash_quality", "经营现金流质量", _cash_quality(financials), "%", 10, lambda v: _band(v, [(-50, 0), (0, 20), (50, 60), (100, 90), (150, 100)]), "经营现金流对利润支撑较好。", "经营现金流对利润的支撑不足。"),
        ],
    )

    valuation = _dimension(
        "valuation",
        "估值",
        weights["valuation"],
        [
            MetricSpec("pe", "动态市盈率", _finite(quote.get("pe")), "倍", 60, _pe_score, "盈利对应的估值处于相对合理区间。", "亏损或估值偏高，安全边际有限。"),
            MetricSpec("pb", "市净率", _finite(quote.get("pb")), "倍", 40, _pb_score, "账面价值估值较为克制。", "市净率偏高或净资产口径异常。"),
        ],
    )

    safety = _dimension(
        "safety",
        "财务安全",
        weights["safety"],
        [
            MetricSpec("debt_ratio", "资产负债率", _finite(financials.get("debt_ratio")), "%", 55, lambda v: _band(v, [(0, 100), (30, 95), (50, 75), (65, 45), (80, 10), (100, 0)]), "负债水平较稳健。", "负债率偏高，偿债压力需要关注。"),
            MetricSpec("current_ratio", "流动比率", _finite(financials.get("current_ratio")), "倍", 25, lambda v: _band(v, [(0, 0), (0.7, 25), (1, 55), (1.5, 85), (2, 100), (5, 80)]), "短期偿债覆盖较充足。", "短期偿债覆盖偏弱。"),
            MetricSpec("cashflow_sign", "每股经营现金流", _finite(financials.get("operating_cashflow_per_share")), "元", 20, lambda v: _band(v, [(-2, 0), (0, 35), (0.5, 65), (2, 90), (5, 100)]), "经营活动产生正向现金流。", "经营现金流偏弱或为负。"),
        ],
    )

    trend = _dimension(
        "trend",
        "趋势与波动",
        weights["trend"],
        [
            MetricSpec("ma20_gap", "相对 20 日均线", technical["ma20_gap"], "%", 20, lambda v: _band(v, [(-20, 0), (-8, 30), (0, 70), (8, 95), (20, 65), (40, 20)]), "价格处于较健康的中短期趋势。", "价格趋势偏弱或短期过热。"),
            MetricSpec("ma60_gap", "相对 60 日均线", technical["ma60_gap"], "%", 20, lambda v: _band(v, [(-30, 0), (-10, 30), (0, 70), (15, 100), (40, 65), (70, 20)]), "中期趋势保持强势且未明显过热。", "中期趋势偏弱或偏离均线过大。"),
            MetricSpec("return_120", "近 120 日涨跌", technical["return_120"], "%", 20, lambda v: _band(v, [(-50, 0), (-20, 25), (0, 65), (25, 95), (60, 75), (120, 25)]), "阶段表现稳中向好。", "阶段走势较弱或涨幅过于极端。"),
            MetricSpec("max_drawdown", "近 120 日最大回撤", technical["max_drawdown"], "%", 20, lambda v: _band(v, [(-70, 0), (-40, 20), (-25, 50), (-15, 75), (-5, 100), (0, 95)]), "历史回撤控制较好。", "历史回撤较深，持有波动风险较高。"),
            MetricSpec("volatility", "年化波动率", technical["volatility"], "%", 20, lambda v: _band(v, [(0, 95), (20, 100), (35, 75), (55, 40), (80, 10), (120, 0)]), "价格波动处于可控区间。", "价格波动率偏高。"),
        ],
    )

    is_st = "ST" in str(quote.get("name", "")).upper()
    price = _finite(quote.get("price"))
    liquidity = _dimension(
        "liquidity",
        "流动性与特殊风险",
        weights["liquidity"],
        [
            MetricSpec("avg_amount", "20 日平均成交额", technical["avg_amount"], "元", 45, lambda v: _band(v, [(0, 0), (1e7, 20), (5e7, 55), (2e8, 85), (8e8, 100)]), "日常成交较活跃，流动性较好。", "成交额偏低，交易冲击成本可能较高。"),
            MetricSpec("turnover", "换手率", _finite(quote.get("turnover")) or technical["avg_turnover"], "%", 25, lambda v: _band(v, [(0, 10), (0.5, 45), (1.5, 85), (5, 100), (12, 65), (25, 20)]), "换手率处于较健康区间。", "换手率过低或过高。"),
            MetricSpec("special_risk", "特殊风险标记", 0.0 if is_st or price is None else 1.0, "", 30, lambda v: v * 100, "未发现 ST 或停牌类显著标记。", "存在 ST 标记或缺少有效价格，风险较高。"),
        ],
    )

    dimensions = [profitability, valuation, safety, trend, liquidity]
    score = round(sum(item["score"] for item in dimensions), 1)
    missing = [
        metric["label"]
        for item in dimensions
        for metric in item["metrics"]
        if metric["status"] == "missing"
    ]
    all_metrics = [metric for item in dimensions for metric in item["metrics"]]
    available_weight = sum(metric["weight"] for metric in all_metrics if metric["status"] != "missing")
    total_weight = sum(metric["weight"] for metric in all_metrics)
    confidence_score = round(available_weight / total_weight * 100) if total_weight else 0
    confidence = "高" if confidence_score >= 85 else "中" if confidence_score >= 60 else "低"
    grade = _grade(score, config["scoring"]["grades"])
    narrative = build_narrative(score, grade, dimensions, missing, confidence)
    return {
        "score": score,
        "grade": grade,
        "confidence": {"level": confidence, "score": confidence_score},
        "dimensions": dimensions,
        "summary": narrative,
        "missing_metrics": missing,
        "calculated_metrics": {key: _round_optional(value) for key, value in technical.items()},
        "trade_plan": build_trade_plan(quote, financials, history, technical, score, grade),
    }


def _grade(score: float, thresholds: dict) -> str:
    if score >= thresholds["excellent"]:
        return "优秀"
    if score >= thresholds["good"]:
        return "良好"
    if score >= thresholds["average"]:
        return "一般"
    if score >= thresholds["weak"]:
        return "偏弱"
    return "高风险"


def _growth_score(value: float) -> float:
    return _band(value, [(-50, 0), (-20, 15), (0, 45), (10, 65), (25, 88), (50, 100), (100, 80), (200, 55)])


def _pe_score(value: float) -> float:
    if value <= 0:
        return 10.0
    return _band(value, [(1, 45), (6, 75), (12, 100), (20, 88), (30, 65), (50, 30), (100, 0)])


def _pb_score(value: float) -> float:
    if value <= 0:
        return 10.0
    return _band(value, [(0.1, 55), (0.8, 100), (2, 85), (4, 50), (8, 10), (15, 0)])


def _finite(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _mean_present(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return float(np.mean(present)) if present else None


def _round_optional(value: float | None) -> float | None:
    return round(value, 2) if value is not None and math.isfinite(value) else None
