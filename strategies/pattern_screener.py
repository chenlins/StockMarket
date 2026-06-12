"""形态选股引擎，对接 Web /api/screen。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from data.fetcher import DataFetcher
from data.filters import is_main_board, is_ordinary_stock, normalize_code
from strategies.patterns import (
    compact_klines,
    history_to_klines,
    pattern_analysis,
    trade_plan,
)


def _safe_float(val, default=None):
    try:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _quote_to_stock(row: pd.Series) -> dict:
    code = normalize_code(row["code"])
    name = str(row.get("name", ""))
    return {
        "code": code,
        "name": name,
        "price": _safe_float(row.get("price"), 0),
        "changePct": _safe_float(row.get("change_pct"), 0),
        "volume": _safe_float(row.get("volume"), 0),
        "amount": _safe_float(row.get("amount"), 0),
        "turnover": _safe_float(row.get("turnover"), 0),
        "marketCap": _safe_float(row.get("market_cap"), 0),
        "isChiNext": code.startswith("300"),
        "isStar": code.startswith("688"),
        "isST": "ST" in name.upper(),
    }


def passes_board_filter(stock: dict, query: dict) -> bool:
    chi_next = query.get("chiNext", "any")
    star = query.get("star", "any")
    st = query.get("st", "exclude")
    code = stock["code"]
    if chi_next == "only" and not stock["isChiNext"]:
        return False
    if chi_next == "exclude" and stock["isChiNext"]:
        return False
    if star == "only" and not stock["isStar"]:
        return False
    if star == "exclude" and stock["isStar"]:
        return False
    if st == "only" and not stock["isST"]:
        return False
    if st == "exclude" and stock["isST"]:
        return False
    # 默认排除创业板/科创板时，仅保留沪深主板，过滤北交所等
    if chi_next == "exclude" and star == "exclude" and not is_main_board(code):
        return False
    return True


def _liquidity_score(stock: dict) -> float:
    return min((stock.get("amount") or 0) / 100_000_000, 30) + min(stock.get("turnover") or 0, 12)


def screen_stocks(fetcher: DataFetcher, query: dict) -> dict:
    min_price = _safe_float(query.get("minPrice"), 0) or 0
    max_price = _safe_float(query.get("maxPrice"), 9999) or 9999
    min_market_cap_input = _safe_float(query.get("minMarketCap"), 0) or 0
    max_market_cap_input = _safe_float(query.get("maxMarketCap"), 99999999) or 99999999
    min_market_cap = min_market_cap_input * 100_000_000
    max_market_cap = max_market_cap_input * 100_000_000
    uses_market_cap = min_market_cap_input > 0 or max_market_cap_input < 999999
    min_volume = (_safe_float(query.get("minVolume"), 0) or 0) * 10000
    max_volume = (_safe_float(query.get("maxVolume"), 999999999) or 999999999) * 10000
    pattern = query.get("pattern") or "rise_price_volume"
    max_inspect = min(max(int(_safe_float(query.get("maxInspect"), 90) or 90), 20), 220)
    effective_inspect = (
        max(max_inspect, 650)
        if pattern in ("limit_up_consolidation", "break_ma5_pressure")
        else max_inspect
    )
    sort_by = query.get("sortBy") or "score"
    analysis_options = {
        "limitMinDays": int(_safe_float(query.get("limitMinDays"), 5) or 5),
        "maGapPct": _safe_float(query.get("maGapPct"), 5) or 5,
        "adjustmentPct": _safe_float(query.get("adjustmentPct"), 10) or 10,
    }

    df = fetcher.get_stock_list()
    source = fetcher.last_source
    stocks = [_quote_to_stock(row) for _, row in df.iterrows()]

    prefiltered = []
    for s in stocks:
        price = s.get("price") or 0
        volume = s.get("volume") or 0
        market_cap = s.get("marketCap") or 0
        if not (min_price <= price <= max_price):
            continue
        if not (min_volume <= volume <= max_volume):
            continue
        if uses_market_cap:
            if not market_cap:
                continue
            if not (min_market_cap <= market_cap <= max_market_cap):
                continue
        if not passes_board_filter(s, query):
            continue
        prefiltered.append(s)

    def _pattern_prefilter(s: dict) -> bool:
        pct = s.get("changePct") or 0
        if pattern == "low_volume_pullback":
            return -6 <= pct <= 2.5
        if pattern == "volume_spike_consolidation":
            return -6 <= pct <= 5
        if pattern == "limit_up_consolidation":
            return -6 <= pct <= 6
        return True

    prefiltered = [s for s in prefiltered if _pattern_prefilter(s)]

    if pattern in ("low_volume_pullback", "volume_spike_consolidation", "limit_up_consolidation"):
        prefiltered.sort(key=_liquidity_score, reverse=True)
    else:
        prefiltered.sort(
            key=lambda s: (s.get("turnover") or 0, s.get("amount") or 0),
            reverse=True,
        )

    base = prefiltered[:effective_inspect]
    results: list[dict] = []

    def _analyze(stock: dict) -> dict | None:
        history = fetcher.get_daily_history(stock["code"], days=90)
        if history.empty or len(history) < 25:
            return None
        klines = history_to_klines(history)
        analysis = pattern_analysis(
            klines,
            pattern,
            {**analysis_options, "code": stock["code"]},
        )
        if not analysis.get("ok"):
            return None
        liquidity = min((stock.get("amount") or 0) / 100_000_000, 18)
        score = round(analysis["score"] + liquidity, 1)
        return {
            **stock,
            "score": score,
            "notes": analysis["notes"],
            "details": analysis["details"],
            "chartMarkers": analysis["markers"],
            "ma5": round(analysis["ma5"] or 0, 2),
            "ma10": round(analysis["ma10"] or 0, 2),
            "ma20": round(analysis["ma20"] or 0, 2),
            "tradePlan": trade_plan(stock, analysis),
            "chartData": compact_klines(klines),
            "lastDate": analysis["last"]["date"],
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_analyze, s): s for s in base}
        for future in as_completed(futures):
            try:
                item = future.result()
                if item:
                    results.append(item)
            except Exception:
                continue

    def _sort_key(item: dict):
        if sort_by == "marketCapAsc":
            return item.get("marketCap") or float("inf")
        if sort_by == "marketCapDesc":
            return -(item.get("marketCap") or 0)
        if sort_by == "amountDesc":
            return -(item.get("amount") or 0)
        if sort_by == "ma10DistanceAsc":
            ma10 = item.get("ma10") or 0
            price = item.get("price") or item.get("tradePlan", {}).get("close") or 0
            return abs(price - ma10) / ma10 if ma10 else float("inf")
        return -item.get("score", 0)

    results.sort(key=_sort_key)
    top = results[:80]
    return {
        "updatedAt": datetime.now().isoformat(),
        "inspected": len(base),
        "totalQuotes": len(stocks),
        "source": source.split("+")[0] if source else "cache",
        "fallbackReason": None,
        "results": top,
        "recommendation": top[0] if top else None,
    }
