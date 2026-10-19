from __future__ import annotations

import math


METHOD = (
    "买入价看现价相对 20 日均线的位置；止损取日波动的 2.2 倍（限制在 4.5%–12%），"
    "若近 20 日低点落在这个区间内则改放在低点下方；止盈按 2–3 倍盈亏比，并受合理市盈率约束。"
    "价格是规则对照，不是收益承诺。"
)


def ensure_trade_plan(payload: dict) -> bool:
    """Attach a trade plan when an older report was stored without one."""
    plan = payload.get("trade_plan")
    if isinstance(plan, dict) and (plan.get("buy_price") or plan.get("available") is False):
        return False
    quote = payload.get("stock") or {}
    raw = payload.get("raw_metrics") or {}
    financials = raw.get("financials") if isinstance(raw.get("financials"), dict) else {}
    technical = payload.get("calculated_metrics") or raw.get("technical") or {}
    if not isinstance(technical, dict):
        technical = {}
    score = _finite(payload.get("score"))
    if score is None:
        return False
    payload["trade_plan"] = build_trade_plan(
        quote,
        financials,
        [],
        technical,
        score,
        str(payload.get("grade") or ""),
    )
    return True


def build_trade_plan(
    quote: dict,
    financials: dict,
    history: list[dict],
    technical: dict,
    score: float,
    grade: str,
) -> dict:
    """Build entry, target, and stop levels that stay consistent with the health score."""
    price = _finite(quote.get("price"))
    if price is None or price <= 0:
        return _unavailable("缺少有效最新价，无法计算买入、止盈和止损。")

    levels = _price_levels(history)
    anchor, fair_pe = _valuation_anchor(
        price,
        _finite(quote.get("pe")),
        _finite(financials.get("eps")),
        _finite(financials.get("roe")),
    )
    is_st = "ST" in str(quote.get("name", "")).upper()
    extended = levels["ma20"] is not None and price > levels["ma20"] * 1.08
    if anchor is not None and anchor < price * 0.90:
        valuation_state = "blocked"
    elif anchor is not None and anchor < price * 0.98:
        valuation_state = "rich"
    elif anchor is not None and anchor >= price * 1.08:
        valuation_state = "cheap"
    else:
        valuation_state = "ok"
    action, action_code, recommended = _action(
        score,
        is_st=is_st,
        extended=extended,
        valuation_state=valuation_state,
    )

    buy, buy_rule = _buy_price(price, levels, action_code, anchor)
    stop, stop_rule = _stop_price(buy, levels, technical)
    take, take_rule = _take_profit(
        buy,
        stop,
        price,
        score,
        anchor,
        recommended,
    )
    buy, stop, take = _normalize_prices(price, buy, stop, take, recommended, anchor)

    risk = buy - stop
    reward_risk = (take - buy) / risk if risk > 0 else None
    if recommended and (reward_risk is None or reward_risk < 1.5):
        action = "赔率不足，暂不买入"
        action_code = "avoid"
        recommended = False

    position, batches = _position(action_code, score, buy, stop)
    history_note = None
    if len(levels["closes"]) < 20:
        history_note = "有效日线不足 20 根，均线和止损都偏粗，这份计划只能作粗略对照。"

    texts = _analyses(
        price=price,
        buy=buy,
        stop=stop,
        take=take,
        score=score,
        grade=grade,
        levels=levels,
        anchor=anchor,
        fair_pe=fair_pe,
        action=action,
        action_code=action_code,
        recommended=recommended,
        buy_rule=buy_rule,
        stop_rule=stop_rule,
        take_rule=take_rule,
        reward_risk=reward_risk or 0,
        valuation_blocked=valuation_state == "blocked",
    )
    return {
        "available": True,
        "reason": "",
        "action": action,
        "action_code": action_code,
        "recommended": recommended,
        "score": score,
        "grade": grade,
        "price": _round_price(price),
        "buy_price": buy,
        "take_profit_price": take,
        "stop_loss_price": stop,
        "buy_vs_price_pct": _pct(price, buy),
        "take_profit_vs_buy_pct": _pct(buy, take),
        "stop_loss_vs_buy_pct": _pct(buy, stop),
        "reward_risk": round(reward_risk, 2) if reward_risk is not None else None,
        "position": position,
        "horizon": "20–60 个交易日",
        "fair_price": _round_price(anchor) if anchor else None,
        "fair_pe": round(fair_pe, 1) if fair_pe else None,
        "batches": batches,
        "history_note": history_note,
        "method": METHOD,
        **texts,
    }


def _unavailable(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "action": "无法定价",
        "action_code": "avoid",
        "recommended": False,
        "score": None,
        "grade": None,
        "price": None,
        "buy_price": None,
        "take_profit_price": None,
        "stop_loss_price": None,
        "buy_vs_price_pct": None,
        "take_profit_vs_buy_pct": None,
        "stop_loss_vs_buy_pct": None,
        "reward_risk": None,
        "position": "不建议动用资金。",
        "horizon": None,
        "fair_price": None,
        "fair_pe": None,
        "batches": [],
        "history_note": None,
        "method": METHOD,
        "summary": reason,
        "buy_analysis": reason,
        "take_profit_analysis": reason,
        "stop_loss_analysis": reason,
        "invalidation": "没有有效价格时，不生成交易计划。",
    }


def _action(
    score: float,
    *,
    is_st: bool,
    extended: bool,
    valuation_state: str,
) -> tuple[str, str, bool]:
    if is_st or score < 40:
        return "回避", "avoid", False
    if valuation_state == "blocked" or score < 55:
        return "暂不买入", "avoid", False
    if valuation_state == "rich" or (valuation_state != "cheap" and score < 70):
        return "等待更优价格", "wait", False
    if extended:
        return "等待回踩", "pullback", True
    if score >= 85:
        return "建议买入", "buy", True
    return "建议分批买入", "buy", True


def _buy_price(price: float, levels: dict, action_code: str, anchor: float | None) -> tuple[float, str]:
    ma20 = levels["ma20"]
    ma60 = levels["ma60"]
    if action_code == "avoid":
        discounted = price * 0.92
        if anchor is not None and anchor < price:
            return max(anchor, price * 0.85), "valuation_reference"
        if ma60 is not None and ma60 < discounted:
            return ma60, "ma60_discount"
        return discounted, "avoid_discount"
    if action_code == "wait":
        discounted = price * 0.96
        candidates = [discounted]
        if ma20 is not None and ma20 < price:
            candidates.append(ma20)
        if anchor is not None and anchor < price:
            candidates.append(max(anchor, price * 0.90))
        return min(candidates), "wait_discount"
    if action_code == "pullback" and ma20 is not None:
        return min(price, ma20), "ma20_pullback"
    if ma20 is not None and price > ma20:
        return (price + ma20) / 2, "mid_ma20"
    return price, "spot"


def _stop_price(buy: float, levels: dict, technical: dict) -> tuple[float, str]:
    volatility = _finite(technical.get("volatility"))
    if volatility is not None and volatility > 1:
        daily = (volatility / 100) / math.sqrt(250)
    else:
        daily = 0.015
    stop_pct = min(0.12, max(0.045, daily * 2.2))
    volatility_stop = buy * (1 - stop_pct)
    recent_low = levels["recent_low"]
    if recent_low is None or recent_low >= buy:
        return volatility_stop, "volatility"
    structural = recent_low * 0.99
    distance = (buy - structural) / buy
    if distance < 0.045:
        return volatility_stop, "swing_too_close"
    if distance > 0.12:
        return volatility_stop, "swing_too_far"
    return structural, "swing"


def _take_profit(
    buy: float,
    stop: float,
    price: float,
    score: float,
    anchor: float | None,
    recommended: bool,
) -> tuple[float, str]:
    risk = max(buy - stop, buy * 0.045)
    multiple = 3.0 if score >= 85 else 2.5 if score >= 70 else 2.0
    technical_target = buy + risk * multiple
    if not recommended:
        if anchor is not None and buy < anchor < technical_target:
            return anchor, "valuation_cap"
        return technical_target, "risk_multiple"
    lifted = max(technical_target, price * 1.08)
    if anchor is not None and anchor > buy * 1.05:
        if lifted > anchor:
            return anchor, "valuation_cap"
        if lifted > technical_target + buy * 0.005:
            return lifted, "lifted_above_spot"
        return technical_target, "risk_multiple"
    if lifted > technical_target + buy * 0.005:
        return lifted, "lifted_above_spot"
    return technical_target, "risk_multiple"


def _normalize_prices(
    price: float,
    buy: float,
    stop: float,
    take: float,
    recommended: bool,
    anchor: float | None,
) -> tuple[float, float, float]:
    buy = min(buy, price)
    buy = _round_price(max(buy, _tick(price)))
    stop = _round_price(stop)
    minimum_gap = max(buy * 0.03, _tick(price) * 3)
    if buy - stop < minimum_gap:
        stop = _round_price(buy - minimum_gap)
    take_floor = buy * (1.08 if recommended else 1.06)
    if recommended:
        take_floor = max(take_floor, price * 1.08)
    rounded_anchor = _round_price(anchor) if anchor and anchor > buy * 1.03 else None
    if rounded_anchor is not None:
        take = min(take, rounded_anchor)
        take_floor = min(take_floor, rounded_anchor)
    take = _round_price(max(take, take_floor))
    if stop >= buy:
        stop = _round_price(buy - minimum_gap)
    return buy, stop, take


def _position(action_code: str, score: float, buy: float, stop: float) -> tuple[str, list[dict]]:
    if action_code == "avoid":
        return "不建议动用资金。", []
    if action_code == "wait":
        return (
            "触及买入价前不建仓；若成交，不超过准备投入该股票资金的 10%。",
            [
                {
                    "label": "观察单",
                    "weight": "10%",
                    "price": buy,
                    "note": "只在价格回到买入价时考虑，未触及就保持空仓。",
                }
            ],
        )
    cap = "30%" if action_code == "buy" and score >= 85 else "20%"
    second = _round_price((buy + stop) / 2)
    if second >= buy:
        second = _round_price(buy - (buy - stop) * 0.4)
    return (
        f"分成两批，合计不超过准备投入该股票资金的 {cap}。",
        [
            {
                "label": "第一批",
                "weight": "60%",
                "price": buy,
                "note": "价格回到买入价时先建立主要仓位。",
            },
            {
                "label": "第二批",
                "weight": "40%",
                "price": second,
                "note": "若继续下探到买入价与止损的中点、且没收盘跌破止损，再补剩余仓位。",
            },
        ],
    )


def _analyses(
    *,
    price: float,
    buy: float,
    stop: float,
    take: float,
    score: float,
    grade: str,
    levels: dict,
    anchor: float | None,
    fair_pe: float | None,
    action: str,
    action_code: str,
    recommended: bool,
    buy_rule: str,
    stop_rule: str,
    take_rule: str,
    reward_risk: float,
    valuation_blocked: bool,
) -> dict:
    buy_gap = _pct(price, buy)
    stop_gap = abs(_pct(buy, stop))
    take_gap = _pct(buy, take)
    ma20_text = _ma_text(price, levels["ma20"])
    buy_analysis = _buy_analysis(price, buy, buy_gap, ma20_text, score, grade, buy_rule, anchor, action_code)
    stop_analysis = _stop_analysis(buy, stop, stop_gap, levels["recent_low"], stop_rule)
    take_analysis = _take_analysis(
        price, buy, take, take_gap, reward_risk, anchor, fair_pe, levels["recent_high"], take_rule
    )
    summary = (
        f"{action}。健康分 {score:.1f}（{grade}），"
        f"买入 { _yuan(buy) }，止损 { _yuan(stop) }，止盈 { _yuan(take) }，"
        f"盈亏比 1:{reward_risk:.2f}。"
    )
    if valuation_blocked and anchor is not None:
        summary += f" 现价高出估值锚约 {abs(_pct(anchor, price)):.1f}%，安全边际不够。"
    if not recommended:
        summary += " 当前不构成买入建议。"
    invalidation = (
        f"收盘价跌破止损价 {_yuan(stop)}，或评级落入“偏弱/高风险”、出现 ST 标记时，计划失效，需要按新数据重算。"
    )
    if action_code in {"buy", "pullback"} and buy < price * 0.995:
        invalidation += f" 若价格不再回落到 {_yuan(buy)}，这份限价计划也不再执行。"
    return {
        "summary": summary,
        "buy_analysis": buy_analysis,
        "take_profit_analysis": take_analysis,
        "stop_loss_analysis": stop_analysis,
        "invalidation": invalidation,
    }


def _buy_analysis(
    price: float,
    buy: float,
    buy_gap: float,
    ma20_text: str,
    score: float,
    grade: str,
    buy_rule: str,
    anchor: float | None,
    action_code: str,
) -> str:
    base = f"现价 {_yuan(price)}。{ma20_text}健康分 {score:.1f}（{grade}）。"
    if buy_rule == "ma20_pullback":
        detail = f"现价相对均线偏高，不追价。买入价放在 20 日均线 {_yuan(buy)}，等回踩再考虑。"
    elif buy_rule == "mid_ma20":
        detail = f"现价略高于 20 日均线，买入价取两者中点 {_yuan(buy)}，留出一段回撤再成交。"
    elif buy_rule == "spot":
        detail = f"价格没有明显冲高，买入价按最新价 {_yuan(buy)} 计算，可以在现价附近挂限价。"
    elif buy_rule == "wait_discount":
        detail = f"评级还不到积极参与的区间，买入价压到 {_yuan(buy)}（较现价 {buy_gap:.1f}%），没回到这个价格就不考虑。"
    elif buy_rule == "valuation_reference" and anchor is not None:
        detail = (
            f"现价仍明显高于估值锚 {_yuan(anchor)}。对照买入价取 {_yuan(buy)}，"
            "用来标出价格需要回落到哪里才重新有安全边际，按当前数据不建议下单。"
        )
        if buy > anchor * 1.02:
            detail += " 估值锚离现价过远，对照价最多下移到现价的 85%，不把遥远的理论价直接当成挂单。"
    else:
        detail = f"当前评级不支持买入。对照价 {_yuan(buy)} 较现价 {buy_gap:.1f}%，只用来标记观察位置。"
    if action_code == "buy" and buy >= price * 0.995 and "不在现价上方追单" not in detail:
        detail += "不在现价上方追单。"
    elif buy < price * 0.995:
        detail += "在价格触及买入价之前保持观望。"
    return base + detail


def _stop_analysis(buy: float, stop: float, stop_gap: float, recent_low: float | None, stop_rule: str) -> str:
    low_text = f"近 20 日低点约 {_yuan(recent_low)}。" if recent_low else "近期低点样本不足。"
    if stop_rule == "swing":
        rule = "这个低点离买入价的距离适中，止损放在低点下方约 1%，避免把正常波动当成趋势破坏。"
    elif stop_rule == "swing_too_close":
        rule = "低点离买入价太近，贴着低点容易被日常波动扫掉，所以改用波动缓冲。"
    elif stop_rule == "swing_too_far":
        rule = "低点离买入价超过 12%，按那个位置止损会放得太宽，所以改用波动上限。"
    else:
        rule = "没有更合适的摆动低点，止损直接按波动缓冲计算。"
    return (
        f"止损价 {_yuan(stop)}，较买入价低 {stop_gap:.1f}%。{low_text}{rule}"
        f"缓冲约为日波动的 2.2 倍，并限制在 4.5%–12%。收盘跌破 {_yuan(stop)}，说明这次承接失败。"
    )


def _take_analysis(
    price: float,
    buy: float,
    take: float,
    take_gap: float,
    reward_risk: float,
    anchor: float | None,
    fair_pe: float | None,
    recent_high: float | None,
    take_rule: str,
) -> str:
    text = f"止盈价 {_yuan(take)}，较买入价高 {take_gap:.1f}%，盈亏比 1:{reward_risk:.2f}。"
    if anchor is not None and fair_pe is not None:
        rounded_anchor = _round_price(anchor)
        if abs(take - rounded_anchor) <= max(_tick(take), 0.05):
            text += f" 这个价格对齐合理市盈率 {fair_pe:.1f} 倍的估值锚，目标不超过估值所支持的位置。"
        elif rounded_anchor > take:
            text += (
                f" 按净资产收益率估算的合理市盈率约 {fair_pe:.1f} 倍，估值锚为 {_yuan(rounded_anchor)}，还在止盈价上方，"
                "所以先按赔率兑现，不把估值上沿当成必达价。"
            )
        else:
            text += f" 合理市盈率约 {fair_pe:.1f} 倍，估值锚 {_yuan(rounded_anchor)} 低于止盈价，上方空间不能按乐观情形外推。"
    else:
        text += " 缺少能对上现价的市盈率或每股收益，止盈只按风险距离的倍数估算。"
    if take_rule == "lifted_above_spot":
        text += (
            f" 只按波动率外推的目标仍低于或贴近现价 {_yuan(price)}，"
            "说明这笔回踩若只博反弹，空间不够。止盈上修到现价上方，成交后要看到价格重新走强才离场。"
        )
    if reward_risk >= 4 and buy < price * 0.97:
        text += " 盈亏比看起来很高，是因为买入价明显低于现价，而止损又靠近买入价。价格不回到买入价，这个赔率不会发生。"
    if recent_high is not None and max(buy, price) * 1.01 < recent_high < take:
        text += f" 上涨过程中先留意近 20 日高点 {_yuan(recent_high)}，这里容易出现第一段抛压。"
    return text


def _price_levels(history: list[dict]) -> dict:
    closes = [_positive(row.get("close")) for row in history or []]
    closes = [value for value in closes if value is not None]
    highs = []
    lows = []
    for row in history or []:
        close = _positive(row.get("close"))
        high = _positive(row.get("high")) or close
        low = _positive(row.get("low")) or close
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
    window_highs = highs[-20:] if len(highs) >= 20 else highs
    window_lows = lows[-20:] if len(lows) >= 20 else lows
    return {
        "closes": closes,
        "ma20": _mean(closes[-20:]) if len(closes) >= 20 else None,
        "ma60": _mean(closes[-60:]) if len(closes) >= 60 else None,
        "recent_high": max(window_highs) if len(window_highs) >= 20 else None,
        "recent_low": min(window_lows) if len(window_lows) >= 20 else None,
    }


def _valuation_anchor(
    price: float,
    pe: float | None,
    eps: float | None,
    roe: float | None,
) -> tuple[float | None, float | None]:
    fair_pe = min(28.0, max(10.0, 8 + roe * 0.6)) if roe is not None and roe > 0 else 15.0
    if pe is not None and pe > 0:
        return price * (fair_pe / pe), fair_pe
    if eps is not None and eps > 0:
        raw = eps * fair_pe
        if price * 0.3 <= raw <= price * 3:
            return raw, fair_pe
    return None, None


def _ma_text(price: float, ma20: float | None) -> str:
    if ma20 is None or ma20 <= 0:
        return "日线不足，暂时没有 20 日均线。"
    gap = (price / ma20 - 1) * 100
    return f"20 日均线 {_yuan(ma20)}，现价偏离 {gap:+.1f}%。"


def _pct(base: float, value: float) -> float:
    if base == 0:
        return 0.0
    return round((value / base - 1) * 100, 2)


def _yuan(value: float) -> str:
    return f"{value:.3f} 元" if value < 1 else f"{value:.2f} 元"


def _round_price(value: float) -> float:
    return round(value, 3 if value < 1 else 2)


def _tick(value: float) -> float:
    return 0.001 if value < 1 else 0.01


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _positive(value) -> float | None:
    number = _finite(value)
    if number is None or number <= 0:
        return None
    return number


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
