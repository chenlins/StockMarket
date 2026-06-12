"""复用参考工具的形态选股策略（量价齐升、缩量回调等）。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def avg(values: list[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def ma(klines: list[dict], n: int) -> float:
    if len(klines) < n:
        return 0.0
    return avg([x["close"] for x in klines[-n:]])


def ma_at(klines: list[dict], n: int, end_exclusive: int) -> float:
    if end_exclusive < n:
        return 0.0
    return avg([x["close"] for x in klines[end_exclusive - n:end_exclusive]])


def week_key(date_text: str) -> str:
    date = datetime.strptime(str(date_text)[:10], "%Y-%m-%d")
    day = date.isoweekday()
    iso = date.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def to_weekly_klines(klines: list[dict]) -> list[dict]:
    weeks: list[dict] = []
    for day in klines:
        key = week_key(day["date"])
        if not weeks or weeks[-1]["weekKey"] != key:
            weeks.append({
                "weekKey": key,
                "date": day["date"],
                "open": day["open"],
                "close": day["close"],
                "high": day["high"],
                "low": day["low"],
                "volume": day["volume"],
            })
        else:
            week = weeks[-1]
            week["close"] = day["close"]
            week["high"] = max(week["high"], day["high"])
            week["low"] = min(week["low"], day["low"])
            week["volume"] += day["volume"]
            week["date"] = day["date"]
    return weeks


def limit_threshold_for_code(code: str) -> float:
    code = str(code)
    if re.match(r"^30[01]", code) or re.match(r"^68[89]", code) or re.match(r"^[89]", code):
        return 19.0
    return 9.5


def history_to_klines(df) -> list[dict]:
    """将 DataFrame 转为形态分析所需的 K 线列表。"""
    klines = []
    rows = df.to_dict("records")
    for i, row in enumerate(rows):
        prev_close = rows[i - 1]["close"] if i > 0 else row["close"]
        pct = ((row["close"] - prev_close) / prev_close * 100) if prev_close else 0.0
        klines.append({
            "date": str(row["date"])[:10],
            "open": float(row["open"]),
            "close": float(row["close"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "volume": float(row["volume"]),
            "pct": float(pct),
        })
    return klines


def pattern_analysis(
    klines: list[dict],
    pattern: str,
    options: dict | None = None,
) -> dict[str, Any]:
    """核心形态分析，逻辑与参考工具 server.js 保持一致。"""
    options = options or {}
    if len(klines) < 25:
        return {"ok": False, "score": 0, "notes": ["K线数据不足"]}

    last = klines[-1]
    prev = klines[-2]
    ma5 = ma(klines, 5)
    ma10 = ma(klines, 10)
    ma20 = ma(klines, 20)
    prev_ma5 = avg([x["close"] for x in klines[-6:-1]])
    vol5 = avg([x["volume"] for x in klines[-6:-1]])
    recent_high = max(x["high"] for x in klines[-20:-1]) if len(klines) >= 21 else 0
    recent_low = min(x["low"] for x in klines[-20:])
    notes: list[str] = []
    details: dict = {}
    markers: list[dict] = []
    ok = True
    score = 0.0

    if pattern == "rise_price_volume":
        price_up = last["close"] > prev["close"]
        volume_up = last["volume"] > prev["volume"] * 1.08 and last["volume"] > vol5 * 1.05
        trend_up = ma5 > ma10 and ma10 >= ma20 * 0.98 and ma5 > prev_ma5
        ok = price_up and volume_up and trend_up
        if price_up:
            score += 24
            notes.append("收盘价较前一日上行")
        if volume_up:
            score += 28
            notes.append("成交量同步放大")
        if trend_up:
            score += 26
            notes.append("短期均线保持上行")

    elif pattern == "low_volume_pullback":
        last3 = klines[-3:]
        recent10 = klines[-10:]
        recent10_high = max(x["high"] for x in recent10)
        recent_high_index = next(
            (i for i, x in enumerate(recent10) if x["high"] == recent10_high), -1
        )
        high_is_before_today = 0 <= recent_high_index < len(recent10) - 1
        high_day = recent10[recent_high_index] if high_is_before_today else None
        up_leg = recent10[max(0, recent_high_index - 3):recent_high_index + 1] if high_is_before_today else []
        pullback_leg = recent10[recent_high_index + 1:] if high_is_before_today else []
        pullback_depth = (recent10_high - last["close"]) / recent10_high if recent10_high else 0
        last3_avg_volume = avg([x["volume"] for x in last3])
        up_leg_avg_volume = avg([x["volume"] for x in up_leg])
        pullback_avg_volume = avg([x["volume"] for x in pullback_leg])
        has_red_or_flat = any(
            last3[i]["close"] <= (klines[-4]["close"] if i == 0 else last3[i - 1]["close"])
            for i in range(len(last3))
        )
        not_already_rebound = (
            last["pct"] <= 2.5
            and last["close"] <= recent10_high * 0.985
            and last["close"] <= ma5 * 1.03
        )
        pullback = (
            high_is_before_today
            and pullback_depth >= 0.015
            and pullback_depth <= 0.12
            and last["close"] >= ma20 * 0.96
        )
        shrink_from_high = bool(high_day and last["volume"] <= high_day["volume"] * 0.68)
        shrink_from_up = (
            up_leg_avg_volume > 0
            and pullback_avg_volume > 0
            and pullback_avg_volume <= up_leg_avg_volume * 0.78
        )
        shrink_current = last["volume"] <= vol5 * 0.88 and last["volume"] <= last3_avg_volume * 1.05
        low_volume = shrink_from_high and shrink_from_up and shrink_current
        up_before = ma10 >= ma20 * 1.005 and last["close"] >= ma20 * 0.96
        ok = pullback and low_volume and up_before and has_red_or_flat and not_already_rebound
        if pullback:
            score += 28
            notes.append("近10日高点已出现在此前交易日，当前回撤1.5%-12%，且未明显跌破20日均线")
        if low_volume:
            score += 30
            notes.append("回调段量能明显低于前高和上涨段量能")
        if up_before:
            score += 24
            notes.append("10日均线仍在20日均线上方，趋势未明显走坏")
        if has_red_or_flat:
            score += 8
            notes.append("近3日出现回调或横盘整理")
        if not_already_rebound:
            score += 12
            notes.append("当日未明显放量拉升，位置仍处于回调观察区")

    elif pattern == "breakout":
        breakout = last["close"] > recent_high * 1.005
        volume_confirm = last["volume"] > vol5 * 1.25
        ok = breakout and volume_confirm
        if breakout:
            score += 40
            notes.append("收盘价突破近20日高点")
        if volume_confirm:
            score += 30
            notes.append("突破伴随放量确认")

    elif pattern == "break_ma5_pressure":
        weekly = to_weekly_klines(klines)
        recent_weeks = weekly[-14:]
        lows = []
        for i in range(1, len(recent_weeks) - 1):
            left, mid, right = recent_weeks[i - 1], recent_weeks[i], recent_weeks[i + 1]
            if mid["low"] <= left["low"] * 1.015 and mid["low"] <= right["low"] * 1.015:
                lows.append({**mid, "index": i})
        triple = lows[-3:]
        current_week = recent_weeks[-1] if recent_weeks else None
        last_week = recent_weeks[-2] if len(recent_weeks) >= 2 else None
        weekly_ma5 = avg([x["close"] for x in recent_weeks[-5:]]) if len(recent_weeks) >= 5 else 0
        prev_weekly_ma5 = avg([x["close"] for x in recent_weeks[-6:-1]]) if len(recent_weeks) >= 6 else 0
        lowest_low = min(x["low"] for x in triple) if triple else 0
        nearest_low = triple[-1] if triple else None
        nearest_not_lowest = bool(nearest_low and nearest_low["low"] > lowest_low * 1.015)
        lows_close = (
            len(triple) == 3
            and max(x["low"] for x in triple) / max(lowest_low, 0.01) <= 1.18
        )
        ma5_touched = (
            weekly_ma5 > 0
            and current_week
            and current_week["low"] <= weekly_ma5 * 1.04
            and current_week["high"] >= weekly_ma5 * 0.98
        )
        pressure_at_ma5 = (
            weekly_ma5 > 0
            and current_week
            and current_week["high"] >= weekly_ma5 * 1.01
            and current_week["close"] <= weekly_ma5 * 1.03
        )
        crossed_ma5 = (
            weekly_ma5 > 0
            and last_week
            and last_week["close"] <= prev_weekly_ma5 * 1.02
            and current_week
            and current_week["high"] >= weekly_ma5
        )
        not_extended = weekly_ma5 > 0 and current_week and current_week["close"] <= weekly_ma5 * 1.08
        weekly_base = current_week and current_week["close"] >= lowest_low * 1.03
        ok = bool(
            len(triple) == 3
            and nearest_not_lowest
            and lows_close
            and ma5_touched
            and pressure_at_ma5
            and crossed_ma5
            and not_extended
            and weekly_base
        )
        if len(triple) == 3:
            score += 28
            notes.append("近14周内出现三个周线低点结构")
            for idx, point in enumerate(triple):
                markers.append({"date": point["date"], "label": f"周底{idx + 1}"})
        if nearest_not_lowest:
            score += 22
            notes.append("最近一个周线低点不是最低点，低点有抬高迹象")
        if lows_close:
            score += 16
            notes.append("三个周线低点处于相对接近的底部区间")
        if ma5_touched and crossed_ma5:
            score += 26
            notes.append("周线级别贯穿或触碰5周线")
        if pressure_at_ma5:
            score += 22
            notes.append("当前周线在5周线附近承压，尚未明显脱离")
        if weekly_base:
            score += 10
            notes.append("当前价格仍处于周线底部结构上方")
        details["weeklyLow1"] = triple[0]["date"] if len(triple) > 0 else "-"
        details["weeklyLow2"] = triple[1]["date"] if len(triple) > 1 else "-"
        details["weeklyLow3"] = triple[2]["date"] if len(triple) > 2 else "-"
        details["weeklyMa5"] = round(weekly_ma5 or 0, 2)
        if nearest_low and lowest_low:
            details["nearestLowLiftPct"] = round(
                ((nearest_low["low"] - lowest_low) / lowest_low) * 100, 2
            )

    elif pattern == "volume_spike_consolidation":
        window = klines[-30:]
        high_index, high_value = -1, 0.0
        for i in range(max(10, len(window) - 10), len(window) - 1):
            if window[i]["high"] > high_value:
                high_value = window[i]["high"]
                high_index = i
        high_day = window[high_index] if high_index >= 0 else None
        runup_start = max(0, high_index - 4)
        before_runup = window[max(0, runup_start - 6):runup_start]
        runup_leg = window[runup_start:high_index + 1] if high_index >= 0 else []
        pullback_leg = window[high_index + 1:] if high_index >= 0 else []
        before_avg_vol = avg([x["volume"] for x in before_runup])
        runup_avg_vol = avg([x["volume"] for x in runup_leg])
        runup_max_vol = max((x["volume"] for x in runup_leg), default=0)
        pullback_avg_vol = avg([x["volume"] for x in pullback_leg])
        pullback_max_vol = max((x["volume"] for x in pullback_leg), default=0)
        pullback_min_low = min((x["low"] for x in pullback_leg), default=0)
        pullback_length = len(pullback_leg)
        runup_gain = (
            (high_day["high"] - runup_leg[0]["low"]) / runup_leg[0]["low"]
            if high_day and runup_leg and runup_leg[0]["low"] > 0
            else 0
        )
        pullback_depth = (high_day["high"] - last["close"]) / high_day["high"] if high_day else 0
        recent_pullback = 1 <= pullback_length <= 7
        volume_expanded = (
            before_avg_vol > 0
            and runup_max_vol >= before_avg_vol * 1.45
            and runup_avg_vol >= before_avg_vol * 1.12
        )
        price_expanded = runup_gain >= 0.055
        volume_shrinking = (
            pullback_avg_vol > 0
            and pullback_avg_vol <= runup_avg_vol * 0.82
            and pullback_max_vol <= runup_max_vol * 1.05
            and last["volume"] <= runup_max_vol * 0.72
        )
        if pullback_length >= 3:
            gradual = pullback_leg[-3:]
            gradual_shrink = sum(
                1 for i, x in enumerate(gradual)
                if i == 0 or x["volume"] <= gradual[i - 1]["volume"] * 1.08
            ) >= 2
        else:
            gradual_shrink = bool(pullback_leg and last["volume"] <= pullback_leg[0]["volume"] * 0.92)
        pullback_to_ma10 = last["low"] <= ma10 * 1.05 and last["close"] >= ma10 * 0.97
        ma10_not_broken = (
            sum(1 for x in pullback_leg if x["close"] >= ma10 * 0.965)
            >= max(1, pullback_length - 1)
        )
        not_too_deep = (
            pullback_depth >= 0.01
            and pullback_depth <= 0.16
            and pullback_min_low >= ma20 * 0.96
        )
        not_rebound = bool(
            last["pct"] <= 3.5 and high_day and last["close"] <= high_day["high"] * 0.985
        )
        ok = bool(
            high_day
            and recent_pullback
            and volume_expanded
            and price_expanded
            and volume_shrinking
            and gradual_shrink
            and pullback_to_ma10
            and ma10_not_broken
            and not_too_deep
            and not_rebound
        )
        if volume_expanded and price_expanded:
            score += 28
            notes.append(f"前段放量拉升，高点出现在 {high_day['date']}")
        if recent_pullback:
            score += 14
            notes.append("高点后处于2-6日短线回踩段")
        if volume_shrinking:
            score += 30
            notes.append("回踩段成交量明显低于拉升段量能")
        if gradual_shrink:
            score += 18
            notes.append("最近几日量能呈逐步缩减状态")
        if pullback_to_ma10:
            score += 26
            notes.append("价格回踩到10日均线附近")
        if ma10_not_broken:
            score += 22
            notes.append("回踩过程中收盘未有效跌破10日均线")
        if not_rebound:
            score += 10
            notes.append("当前尚未重新拉回前高附近")

    elif pattern == "limit_up_consolidation":
        limit_min_days = max(3, int(options.get("limitMinDays") or 5))
        ma_gap_pct = max(1, float(options.get("maGapPct") or 5)) / 100
        adjustment_pct = max(3, float(options.get("adjustmentPct") or 10)) / 100
        code = str(options.get("code") or "")
        window = klines[-25:]
        limit_index, limit_strength = -1, 0.0
        for i in range(1, len(window) - 3):
            day, prev_day = window[i], window[i - 1]
            gain = ((day["close"] - prev_day["close"]) / prev_day["close"] * 100) if prev_day["close"] else 0
            close_near_high = day["high"] > 0 and day["close"] >= day["high"] * 0.985
            limit_like = gain >= limit_threshold_for_code(code) and close_near_high
            if limit_like and gain > limit_strength:
                limit_strength = gain
                limit_index = i
        limit_day = window[limit_index] if limit_index >= 0 else None
        after_limit = window[limit_index + 1:] if limit_index >= 0 else []
        after_high = max((x["high"] for x in after_limit), default=0)
        after_low = min((x["low"] for x in after_limit), default=0)
        after_close_low = min((x["close"] for x in after_limit), default=0)
        after_close_high = max((x["close"] for x in after_limit), default=0)
        first_after_close = after_limit[0]["close"] if after_limit else 0
        last3 = after_limit[-3:]
        days_after = len(after_limit)
        has_enough_days = limit_min_days <= days_after <= 18
        no_big_drop = (
            limit_day
            and after_close_low >= limit_day["close"] * 0.94
            and after_low >= limit_day["low"] * 0.97
        )
        sideways = (
            limit_day
            and after_close_low > 0
            and (after_close_high - after_close_low) / after_close_low <= 0.105
            and after_high <= limit_day["close"] * 1.10
            and after_low >= limit_day["close"] * 0.92
        )
        flat_slope = (
            abs(last["close"] - first_after_close) / first_after_close <= 0.075
            if first_after_close > 0
            else False
        )
        not_chasing = limit_day and last["close"] <= limit_day["close"] * 1.09 and last["pct"] <= 3
        no_second_breakout = not any(
            x["pct"] >= 6 or x["close"] >= limit_day["close"] * 1.11
            for x in after_limit
        ) if limit_day else False

        def _holds_ma(day):
            abs_idx = next((j for j, k in enumerate(klines) if k["date"] == day["date"]), -1)
            if abs_idx < 0:
                return False
            day_ma5 = ma_at(klines, 5, abs_idx + 1)
            day_ma10 = ma_at(klines, 10, abs_idx + 1)
            return day["close"] >= day_ma5 * 0.96 or day["close"] >= day_ma10 * 0.985

        holds_ma5_ma10 = (
            last["close"] >= ma10 * 0.985
            and sum(1 for x in last3 if _holds_ma(x)) >= max(1, len(last3) - 1)
        )
        ma_gap = abs(ma5 - ma10) / ma10 if ma10 > 0 else 999
        ma5_ma10_close = ma_gap <= ma_gap_pct
        small_adjustment = (
            limit_day
            and last["close"] >= limit_day["close"] * (1 - adjustment_pct)
            and last["close"] <= limit_day["close"] * (1 + adjustment_pct)
            and abs(last["close"] - limit_day["close"]) / limit_day["close"] <= adjustment_pct
        )
        volume_not_exploding = (
            avg([x["volume"] for x in after_limit]) <= limit_day["volume"] * 1.15
            and last["volume"] <= limit_day["volume"] * 1.25
        ) if after_limit and limit_day else False
        still_active = limit_day and last["close"] >= limit_day["close"] * 0.97
        ok = bool(
            limit_day
            and has_enough_days
            and small_adjustment
            and ma5_ma10_close
            and no_big_drop
            and sideways
            and flat_slope
            and not_chasing
            and no_second_breakout
            and holds_ma5_ma10
            and volume_not_exploding
            and still_active
        )
        if limit_day:
            score += 30
            notes.append(f"近25日出现涨停或接近涨停，日期 {limit_day['date']}")
            markers.append({"date": limit_day["date"], "label": "涨停"})
        if has_enough_days:
            score += 14
            notes.append(f"涨停后已小幅调整{limit_min_days}天以上")
        if small_adjustment:
            score += 18
            notes.append("调整幅度较小，仍围绕涨停日收盘价附近运行")
        if ma5_ma10_close:
            score += 20
            notes.append("MA5 与 MA10 已靠近，短期均线收敛")
        if no_big_drop:
            score += 22
            notes.append("涨停后没有出现明显破位回撤")
        if sideways:
            score += 22
            notes.append("涨停后维持横盘或小幅抬升整理")
        if flat_slope:
            score += 14
            notes.append("整理段整体斜率较平，没有明显趋势拉升")
        if no_second_breakout:
            score += 14
            notes.append("涨停后尚未出现二次放量突破")
        if holds_ma5_ma10:
            score += 18
            notes.append("整理期间股价保持在短期均线附近或上方")
        if volume_not_exploding:
            score += 12
            notes.append("整理阶段量能没有异常失控放大")
        if not_chasing:
            score += 10
            notes.append("当前没有明显追高脱离整理区")
        details["limitDate"] = limit_day["date"] if limit_day else "-"
        details["daysAfterLimit"] = days_after
        details["maGapPct"] = round(ma_gap * 100, 2)
        if limit_day:
            details["adjustmentFromLimitPct"] = round(
                ((last["close"] - limit_day["close"]) / limit_day["close"]) * 100, 2
            )
            details["limitClose"] = round(limit_day["close"], 2)
        if after_close_low > 0:
            details["rangePct"] = round(
                ((after_close_high - after_close_low) / after_close_low) * 100, 2
            )

    else:
        ok = True
        score += 18
        notes.append("未限制形态，仅按基础条件排序")

    risk = (last["close"] - recent_low) / last["close"] if last["close"] > 0 else 0
    if risk < 0.12:
        score += 12
        notes.append("离近20日低点不远，止损空间可控")
    if last["close"] > ma5 and last["close"] > ma10:
        score += 10

    return {
        "ok": ok,
        "score": score,
        "notes": notes,
        "details": details,
        "markers": markers,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "recentHigh": recent_high,
        "recentLow": recent_low,
        "last": last,
    }


def trade_plan(stock: dict, analysis: dict) -> dict:
    close = analysis["last"]["close"] or stock.get("price") or 0
    support = min(analysis["recentLow"], close * 0.96)
    stop_loss = max(support, close * 0.92)
    risk = max(close - stop_loss, close * 0.025)
    take_profit = close + risk * 2
    pullback_buy = max(analysis.get("ma5") or close * 0.99, close * 0.985)
    return {
        "buyPrice": round(pullback_buy, 2),
        "stopLoss": round(stop_loss, 2),
        "takeProfit": round(take_profit, 2),
        "close": round(close, 2),
    }


def compact_klines(klines: list[dict]) -> list[dict]:
    return [
        {
            "date": x["date"],
            "open": round(x["open"], 2),
            "close": round(x["close"], 2),
            "high": round(x["high"], 2),
            "low": round(x["low"], 2),
            "volume": int(x["volume"]),
        }
        for x in klines[-30:]
    ]
