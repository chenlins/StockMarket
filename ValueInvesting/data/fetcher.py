from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

import akshare as ak
import pandas as pd
import requests

from .storage import CacheStorage


class DataFetchError(RuntimeError):
    """Raised when live data and acceptable cached data are both unavailable."""


class StockNotFoundError(LookupError):
    """Raised when a valid-looking code is not present in the A-share quote list."""


def normalize_code(value: str) -> str:
    code = re.sub(r"\D", "", str(value).strip())
    if len(code) != 6:
        raise ValueError("请输入 6 位 A 股代码，例如 600519。")
    if not code.startswith(("0", "3", "6", "8", "4")):
        raise ValueError("该代码不属于常见 A 股代码范围。")
    return code


@dataclass
class DataResult:
    data: Any
    source: str
    fetched_at: str
    stale: bool = False

    def meta(self) -> dict:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
        }


class StockDataFetcher:
    def __init__(self, storage: CacheStorage, config: dict):
        self.storage = storage
        self.config = config
        self.interval = float(config.get("request_interval_seconds", 0.35))
        self.retries = int(config.get("retries", 2))
        self.stale_seconds = float(config.get("allow_stale_cache_days", 14)) * 86400
        self._last_request = 0.0
        self._request_lock = threading.Lock()

    def _throttle(self) -> None:
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self._last_request = time.monotonic()

    def _retry(self, operation: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                self._throttle()
                return operation()
            except StockNotFoundError:
                raise
            except Exception as error:  # public data endpoints fail in varied ways
                last_error = error
                if attempt + 1 < self.retries:
                    time.sleep(0.8 * (attempt + 1))
        if isinstance(last_error, DataFetchError):
            raise last_error
        raise DataFetchError(str(last_error or "数据源请求失败"))

    def _first_available(
        self,
        attempts: list[tuple[str, float, Callable[[], Any], str]],
        *,
        allow_stale: bool = True,
    ) -> DataResult:
        """Try every live source before falling back to the newest cached copy."""
        errors: list[str] = []
        not_found: StockNotFoundError | None = None
        stale_hits: list[dict] = []
        for key, max_age_seconds, fetch, source in attempts:
            fresh = self.storage.get(key, max_age_seconds)
            if fresh:
                return DataResult(fresh["payload"], fresh["source"], fresh["fetched_at"])
            try:
                payload = _to_json_value(self._retry(fetch))
                self.storage.put(key, payload, source)
                saved = self.storage.get(key)
                assert saved is not None
                return DataResult(saved["payload"], source, saved["fetched_at"])
            except StockNotFoundError as error:
                not_found = error
            except Exception as error:
                errors.append(f"{source}：{error}")
                stale = self.storage.get(key, self.stale_seconds)
                if stale:
                    stale_hits.append(stale)
        if allow_stale and stale_hits:
            stale = max(stale_hits, key=lambda item: item["fetched_at"])
            return DataResult(stale["payload"], stale["source"], stale["fetched_at"], True)
        if not_found is not None and not errors:
            raise not_found
        raise DataFetchError("；".join(errors) or str(not_found or "数据源不可用"))

    def _cached_or_fetch(
        self,
        key: str,
        max_age_seconds: float,
        fetch: Callable[[], Any],
        source: str,
    ) -> DataResult:
        return self._first_available([(key, max_age_seconds, fetch, source)])

    def get_quote(self, code: str) -> DataResult:
        code = normalize_code(code)
        ttl = float(self.config.get("quote_cache_minutes", 15)) * 60
        try:
            return self._first_available(
                [
                    (f"quote:{code}:tencent", ttl, lambda: _fetch_tencent_quote(code), "腾讯证券实时行情"),
                    (f"quote:{code}:eastmoney-rt", ttl, lambda: _fetch_eastmoney_quote(code), "东方财富实时行情"),
                ],
                allow_stale=False,
            )
        except StockNotFoundError:
            raise
        except DataFetchError as error:
            cached = self._quote_from_list_cache(code)
            if cached is not None:
                return cached
            raise error

    def _quote_from_list_cache(self, code: str) -> DataResult | None:
        found_list = False
        best: DataResult | None = None
        for key in ("quotes:a-share:eastmoney", "quotes:a-share:sina"):
            cached = self.storage.get(key, self.stale_seconds)
            if not cached or not isinstance(cached["payload"], list):
                continue
            found_list = True
            for row in cached["payload"]:
                if not isinstance(row, dict):
                    continue
                if row.get("code") and "price" in row:
                    row_code = str(row.get("code"))
                    quote = row
                else:
                    row_code = _row_code(row)
                    quote = _normalize_quote(row, row_code) if row_code else None
                if row_code != code or quote is None:
                    continue
                candidate = DataResult(quote, cached["source"], cached["fetched_at"], True)
                if best is None or candidate.fetched_at > best.fetched_at:
                    best = candidate
                break
        if best is not None:
            return best
        if found_list:
            raise StockNotFoundError(f"未找到股票代码 {code}，请确认代码仍在上市交易。")
        return None

    def get_quote_list(self) -> DataResult:
        """Return normalized A-share quotes for startup ranking and single-stock lookup."""
        ttl = float(self.config.get("quote_cache_minutes", 15)) * 60

        def fetch_sina() -> list[dict]:
            frame = ak.stock_zh_a_spot()
            if frame is None or frame.empty:
                raise DataFetchError("新浪实时行情返回为空")
            return frame.to_dict(orient="records")

        try:
            result = self._first_available(
                [("quotes:a-share:eastmoney", ttl, _fetch_eastmoney_spot_rows, "东方财富实时行情")],
                allow_stale=False,
            )
        except DataFetchError as eastmoney_error:
            stale = self.storage.get("quotes:a-share:eastmoney", self.stale_seconds)
            if stale and isinstance(stale["payload"], list) and stale["payload"]:
                result = DataResult(stale["payload"], stale["source"], stale["fetched_at"], True)
            else:
                try:
                    result = self._first_available(
                        [("quotes:a-share:sina", ttl, fetch_sina, "新浪财经实时行情")]
                    )
                except DataFetchError as sina_error:
                    raise DataFetchError(
                        f"东方财富实时行情：{eastmoney_error}；新浪财经实时行情：{sina_error}"
                    ) from sina_error
        quotes = _coerce_quotes(result.data)
        if not quotes:
            raise DataFetchError("实时行情返回为空")
        return DataResult(quotes, result.source, result.fetched_at, result.stale)

    def get_hot_rank(self, limit: int = 500) -> DataResult:
        """Take the popularity list's top ranks, then fill from the rising list if short."""
        ttl = float(self.config.get("hot_rank_cache_minutes", 30)) * 60
        page_size = 100

        def fetch_hot_rank() -> list[dict]:
            endpoints = (
                ("https://emappdata.eastmoney.com/stockrank/getAllCurrentList", "rk", "人气榜"),
                ("https://emappdata.eastmoney.com/stockrank/getAllHisRcList", "hrcrk", "飙升榜"),
            )
            ranked: list[dict] = []
            seen: set[str] = set()
            for url, rank_field, list_name in endpoints:
                if len(ranked) >= limit:
                    break
                page_no = 1
                while len(ranked) < limit and page_no <= 20:
                    response = requests.post(
                        url,
                        json={
                            "appId": "appId01",
                            "globalId": "786e4c21-70dc-435a-93bb-38",
                            "marketType": "",
                            "pageNo": page_no,
                            "pageSize": page_size,
                        },
                        timeout=20,
                    )
                    response.raise_for_status()
                    rows = response.json().get("data") or []
                    if not rows:
                        break
                    added = 0
                    for row in rows:
                        code = _row_code({"代码": row.get("sc")})
                        if not code or code in seen or not code.startswith(("0", "3", "6", "8", "4")):
                            continue
                        seen.add(code)
                        ranked.append(
                            {
                                "code": code,
                                "heat_rank": len(ranked) + 1,
                                "source_rank": int(_number(row.get(rank_field)) or len(ranked) + 1),
                                "heat_list": list_name,
                            }
                        )
                        added += 1
                        if len(ranked) >= limit:
                            return ranked
                    if added == 0 or len(rows) < page_size:
                        break
                    page_no += 1
            if not ranked:
                raise DataFetchError("东方财富人气榜返回为空")
            return ranked

        return self._cached_or_fetch(
            f"hot-rank:eastmoney:combined:{limit}:v3",
            ttl,
            fetch_hot_rank,
            "东方财富人气榜与飙升榜",
        )

    def get_history(self, code: str, days: int = 120) -> DataResult:
        code = normalize_code(code)
        ttl = float(self.config.get("history_cache_hours", 12)) * 3600
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        def fetch_eastmoney_history() -> list[dict]:
            return _fetch_eastmoney_history(code, start, end, days)

        def fetch_sina_history() -> list[dict]:
            market_code = (
                f"sh{code}" if code.startswith("6") else
                f"bj{code}" if code.startswith(("4", "8")) else
                f"sz{code}"
            )
            frame = ak.stock_zh_a_daily(
                symbol=market_code,
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
            if frame is None or frame.empty:
                raise DataFetchError("新浪历史行情返回为空")
            return _normalize_history_frame(frame, days)

        return self._first_available(
            [
                (
                    f"history:{code}:{days}:tencent",
                    ttl,
                    lambda: _fetch_tencent_history(code, days),
                    "腾讯证券前复权日线",
                ),
                (f"history:{code}:{days}:eastmoney", ttl, fetch_eastmoney_history, "东方财富前复权日线"),
                (f"history:{code}:{days}:sina", ttl, fetch_sina_history, "新浪财经前复权日线"),
            ]
        )

    def get_financials(self, code: str) -> DataResult:
        code = normalize_code(code)
        ttl = float(self.config.get("financial_cache_hours", 24)) * 3600

        def fetch_financials() -> dict:
            frame = self._fetch_financial_frame(code)
            if frame is None or frame.empty:
                raise DataFetchError("财务指标返回为空")
            row = _latest_financial_row(frame)
            return _normalize_financial_row(row)

        return self._cached_or_fetch(
            f"financials:{code}",
            ttl,
            fetch_financials,
            "AkShare 财务分析指标",
        )

    def get_market_financials(self) -> DataResult:
        """Fetch one report-period snapshot for the whole market in a single paged request."""
        report_date = _latest_complete_report_date()
        ttl = float(self.config.get("financial_cache_hours", 24)) * 3600

        def fetch_market_financials() -> dict:
            frame = ak.stock_yjbb_em(date=report_date)
            if frame is None or frame.empty:
                raise DataFetchError(f"{report_date} 批量财务数据返回为空")
            stocks: dict[str, dict] = {}
            annualization = {"03": 4.0, "06": 2.0, "09": 4.0 / 3.0, "12": 1.0}[report_date[4:6]]
            for row in frame.to_dict(orient="records"):
                code = _row_code({"代码": row.get("股票代码")})
                if not code:
                    continue
                revenue = _number(row.get("营业总收入-营业总收入"))
                profit = _number(row.get("净利润-净利润"))
                eps = _number(row.get("每股收益"))
                book_value = _number(row.get("每股净资产"))
                stocks[code] = {
                    "report_date": report_date,
                    "roe": _number(row.get("净资产收益率")),
                    "net_margin": profit / revenue * 100 if profit is not None and revenue else None,
                    "revenue_growth": _number(row.get("营业总收入-同比增长")),
                    "profit_growth": _number(row.get("净利润-同比增长")),
                    "debt_ratio": None,
                    "current_ratio": None,
                    "operating_cashflow_per_share": _number(row.get("每股经营现金流量")),
                    "eps": eps,
                    "book_value_per_share": book_value,
                    "annualized_eps": eps * annualization if eps is not None else None,
                }
            if not stocks:
                raise DataFetchError("批量财务数据无法识别股票代码")
            return {"report_date": report_date, "stocks": stocks}

        return self._cached_or_fetch(
            f"financials:market:{report_date}",
            ttl,
            fetch_market_financials,
            "东方财富批量业绩报表",
        )

    def _fetch_financial_frame(self, code: str) -> pd.DataFrame:
        errors: list[str] = []
        candidates: list[Callable[[], pd.DataFrame]] = []
        em_function = getattr(ak, "stock_financial_analysis_indicator_em", None)
        if callable(em_function):
            candidates.append(lambda: em_function(symbol=code, indicator="按报告期"))
        legacy_function = getattr(ak, "stock_financial_analysis_indicator", None)
        if callable(legacy_function):
            candidates.append(lambda: legacy_function(symbol=code))

        for operation in candidates:
            try:
                frame = self._retry(operation)
                if frame is not None and not frame.empty:
                    return frame
            except Exception as error:
                errors.append(str(error))
        raise DataFetchError("；".join(errors) or "当前 AkShare 版本不支持财务指标接口")


_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}
_EASTMONEY_LIST_URLS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
)
_EASTMONEY_QUOTE_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/get",
    "https://82.push2delay.eastmoney.com/api/qt/stock/get",
    "https://push2.eastmoney.com/api/qt/stock/get",
    "https://82.push2.eastmoney.com/api/qt/stock/get",
)
_EASTMONEY_KLINE_URLS = (
    "https://82.push2delay.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://push2delay.eastmoney.com/api/qt/stock/kline/get",
    "https://push2.eastmoney.com/api/qt/stock/kline/get",
)


def _request_json(urls: tuple[str, ...], params: dict, headers: dict, timeout: float = 8) -> dict:
    last_error: Exception | None = None
    for url in urls:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("data"):
                return payload
            last_error = DataFetchError("行情接口返回空数据")
        except Exception as error:
            last_error = error
    raise DataFetchError(str(last_error or "行情接口不可用"))


def _diff_rows(diff: Any) -> list[dict]:
    if isinstance(diff, list):
        return [row for row in diff if isinstance(row, dict)]
    if isinstance(diff, dict):
        return [row for row in diff.values() if isinstance(row, dict)]
    return []


def _eastmoney_secid(code: str) -> str:
    market = "1" if code.startswith("6") else "0"
    return f"{market}.{code}"


def _tencent_symbol(code: str) -> str:
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sz{code}"


def _fetch_eastmoney_spot_rows() -> list[dict]:
    headers = {**_DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
        "fields": "f2,f3,f6,f8,f9,f10,f12,f14,f20,f23",
    }
    first = _request_json(_EASTMONEY_LIST_URLS, params, headers, timeout=12)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    rows = _diff_rows(data.get("diff"))
    page_size = len(rows) or 100
    if total <= 0 or not rows:
        raise DataFetchError("实时行情返回为空")
    pages = math.ceil(total / page_size)
    for page in range(2, pages + 1):
        params["pn"] = str(page)
        time.sleep(0.05)
        payload = _request_json(_EASTMONEY_LIST_URLS, params, headers, timeout=12)
        rows.extend(_diff_rows((payload.get("data") or {}).get("diff")))
    if len(rows) < total * 0.9:
        raise DataFetchError(f"实时行情不完整：{len(rows)}/{total}")
    normalized = []
    for item in rows:
        code = str(item.get("f12") or "").strip()
        if len(code) != 6 or not code.isdigit():
            continue
        normalized.append(
            {
                "代码": code,
                "名称": item.get("f14") or "",
                "最新价": item.get("f2"),
                "涨跌幅": item.get("f3"),
                "成交额": item.get("f6"),
                "换手率": item.get("f8"),
                "市盈率-动态": item.get("f9"),
                "量比": item.get("f10"),
                "总市值": item.get("f20"),
                "市净率": item.get("f23"),
            }
        )
    if not normalized:
        raise DataFetchError("实时行情无法识别股票代码")
    return normalized


def _fetch_eastmoney_quote(code: str) -> dict:
    headers = {**_DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    payload = _request_json(
        _EASTMONEY_QUOTE_URLS,
        {
            "fltt": "2",
            "invt": "2",
            "secid": _eastmoney_secid(code),
            "fields": "f43,f57,f58,f48,f50,f116,f162,f167,f168,f170",
        },
        headers,
    )
    data = payload.get("data") or {}
    name = str(data.get("f58") or "").strip()
    price = _number(data.get("f43"))
    if not name or price is None or price <= 0:
        raise DataFetchError("东方财富单股行情返回为空")
    return {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": _number(data.get("f170")),
        "amount": _number(data.get("f48")),
        "turnover": _number(data.get("f168")),
        "pe": _number(data.get("f162")),
        "pb": _number(data.get("f167")),
        "market_cap": _number(data.get("f116")),
        "volume_ratio": _number(data.get("f50")),
    }


def _fetch_eastmoney_history(code: str, start: str, end: str, days: int) -> list[dict]:
    headers = {**_DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "101",
        "fqt": "1",
        "secid": _eastmoney_secid(code),
        "beg": start,
        "end": end,
    }
    last_error: Exception | None = None
    for url in _EASTMONEY_KLINE_URLS:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=8)
            response.raise_for_status()
            klines = (response.json().get("data") or {}).get("klines") or []
            if not klines:
                last_error = DataFetchError("历史行情返回为空")
                continue
            frame = pd.DataFrame([item.split(",") for item in klines])
            frame.columns = [
                "日期",
                "开盘",
                "收盘",
                "最高",
                "最低",
                "成交量",
                "成交额",
                "振幅",
                "涨跌幅",
                "涨跌额",
                "换手率",
            ][: frame.shape[1]]
            normalized = _normalize_history_frame(frame, days)
            if not normalized:
                last_error = DataFetchError("历史行情返回为空")
                continue
            return normalized
        except Exception as error:
            last_error = error
    raise DataFetchError(str(last_error or "历史行情不可用"))


def _fetch_tencent_quote(code: str) -> dict:
    response = requests.get(
        f"https://qt.gtimg.cn/q={_tencent_symbol(code)}",
        headers={**_DEFAULT_HEADERS, "Referer": "https://gu.qq.com/"},
        timeout=8,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    return _parse_tencent_quote(code, response.text)


def _parse_tencent_quote(code: str, text: str) -> dict:
    if "~" not in text:
        raise DataFetchError("腾讯实时行情返回为空")
    parts = text.split("~")
    if len(parts) < 47 or not str(parts[1]).strip() or str(parts[2]).strip() != code:
        raise DataFetchError("腾讯实时行情返回为空")
    price = _number(parts[3])
    if price is None or price <= 0:
        raise DataFetchError("腾讯实时行情无有效价格")
    amount_wan = _number(parts[37])
    market_cap_yi = _number(parts[45])
    dynamic_pe = _number(parts[52]) if len(parts) > 52 else None
    return {
        "code": code,
        "name": str(parts[1]).strip(),
        "price": price,
        "change_pct": _number(parts[32]),
        "amount": amount_wan * 10000 if amount_wan is not None else None,
        "turnover": _number(parts[38]),
        "pe": dynamic_pe if dynamic_pe is not None else _number(parts[39]),
        "pb": _number(parts[46]),
        "market_cap": market_cap_yi * 100_000_000 if market_cap_yi is not None else None,
        "volume_ratio": _number(parts[49]) if len(parts) > 49 else None,
    }


def _fetch_tencent_history(code: str, days: int) -> list[dict]:
    symbol = _tencent_symbol(code)
    headers = {**_DEFAULT_HEADERS, "Referer": "https://gu.qq.com/"}
    last_error: Exception | None = None
    for url in (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    ):
        try:
            response = requests.get(
                url,
                params={"param": f"{symbol},day,,,{max(int(days), 1)},qfq"},
                headers=headers,
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            data = (payload.get("data") or {}).get(symbol) or {}
            parsed = _parse_tencent_history_rows(data.get("qfqday") or data.get("day") or [], days)
            if parsed:
                return parsed
            last_error = DataFetchError("腾讯历史行情返回为空")
        except Exception as error:
            last_error = error
    raise DataFetchError(str(last_error or "腾讯历史行情不可用"))


def _parse_tencent_history_rows(rows: list, days: int) -> list[dict]:
    parsed = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        amount = _number(row[8]) if len(row) > 8 else None
        turnover = _number(row[7]) if len(row) > 7 else None
        parsed.append(
            {
                "date": str(row[0]),
                "open": _number(row[1]),
                "close": _number(row[2]),
                "high": _number(row[3]),
                "low": _number(row[4]),
                "volume": _number(row[5]),
                "amount": amount * 10000 if amount is not None else None,
                "turnover": turnover,
            }
        )
    parsed = [row for row in parsed if row["date"] and row["close"] is not None]
    return parsed[-days:]


def _coerce_quotes(rows: Any) -> list[dict]:
    quotes = []
    if not isinstance(rows, list):
        return quotes
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("code") and "price" in row:
            quotes.append(row)
            continue
        row_code = _row_code(row)
        if row_code:
            quotes.append(_normalize_quote(row, row_code))
    return quotes


def resolve_scoring_financials(
    fetcher,
    code: str,
    market_result: DataResult | None = None,
) -> DataResult:
    """Return one period-consistent financial set, with market data as fallback."""
    scoring_fields = (
        "roe",
        "net_margin",
        "revenue_growth",
        "profit_growth",
        "debt_ratio",
        "current_ratio",
        "operating_cashflow_per_share",
        "eps",
    )
    individual: DataResult | None = None
    individual_error: Exception | None = None
    try:
        individual = fetcher.get_financials(code)
    except (DataFetchError, RuntimeError, ValueError) as error:
        individual_error = error

    needs_supplement = individual is None or any(
        individual.data.get(field) is None for field in scoring_fields
    )
    if needs_supplement and market_result is None:
        try:
            market_result = fetcher.get_market_financials()
        except (AttributeError, DataFetchError, RuntimeError, ValueError):
            market_result = None

    market_financials: dict = {}
    if market_result is not None and isinstance(market_result.data, dict):
        market_financials = market_result.data.get("stocks", {}).get(code, {}) or {}

    if individual is None and not market_financials:
        raise DataFetchError(str(individual_error or "财务数据不可用"))

    individual_period = _report_period(individual.data.get("report_date")) if individual else None
    market_period = _report_period(market_financials.get("report_date"))
    same_period = bool(individual_period and individual_period == market_period)

    if individual is not None and market_financials and same_period:
        merged = dict(market_financials)
        merged.update({key: value for key, value in individual.data.items() if value is not None})
        source = f"{individual.source}（同报告期缺失项由{market_result.source}补齐）"
        fetched_at = individual.fetched_at or market_result.fetched_at
        stale = individual.stale or market_result.stale
    elif individual is not None:
        merged = dict(individual.data)
        source = individual.source
        if market_financials and individual_period != market_period:
            source += "（批量财务报告期不同，未跨期合并）"
        fetched_at = individual.fetched_at
        stale = individual.stale
    else:
        merged = dict(market_financials)
        source = f"{market_result.source}（单股接口失败，批量财务回退）"
        fetched_at = market_result.fetched_at
        stale = market_result.stale

    return DataResult(merged, source, fetched_at, stale)


def _report_period(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:8] if len(digits) >= 8 else None


def _latest_financial_row(frame: pd.DataFrame) -> dict:
    date_columns = ("日期", "报告期", "报告日期", "截止日期")
    for column in date_columns:
        if column in frame.columns:
            parsed = pd.to_datetime(frame[column], errors="coerce")
            if parsed.notna().any():
                return frame.loc[parsed.idxmax()].to_dict()
    return frame.iloc[-1].to_dict()


def _latest_complete_report_date() -> str:
    reference = datetime.now() - timedelta(days=50)
    quarter_month = ((reference.month - 1) // 3) * 3
    year = reference.year
    if quarter_month == 0:
        year -= 1
        quarter_month = 12
    day = 31 if quarter_month in (3, 12) else 30
    return f"{year:04d}{quarter_month:02d}{day:02d}"


def _normalize_history_frame(frame: pd.DataFrame, days: int) -> list[dict]:
    def column(*names: str) -> pd.Series:
        for name in names:
            if name in frame.columns:
                return frame[name]
        return pd.Series([None] * len(frame), index=frame.index)

    normalized = pd.DataFrame(
        {
            "date": column("日期", "date").astype(str),
            "open": pd.to_numeric(column("开盘", "open"), errors="coerce"),
            "high": pd.to_numeric(column("最高", "high"), errors="coerce"),
            "low": pd.to_numeric(column("最低", "low"), errors="coerce"),
            "close": pd.to_numeric(column("收盘", "close"), errors="coerce"),
            "volume": pd.to_numeric(column("成交量", "volume"), errors="coerce"),
            "amount": pd.to_numeric(column("成交额", "amount"), errors="coerce"),
            "turnover": pd.to_numeric(column("换手率", "turnover"), errors="coerce"),
        }
    ).dropna(subset=["date", "close"])
    return normalized.tail(days).to_dict(orient="records")


def _normalize_financial_row(row: dict) -> dict:
    def first(*names: str) -> float | None:
        for name in names:
            if name in row:
                value = _number(row.get(name))
                if value is not None:
                    return value
        return None

    report_date = next(
        (str(row[name]) for name in ("日期", "报告期", "报告日期", "截止日期") if row.get(name) is not None),
        None,
    )
    return {
        "report_date": report_date,
        "roe": first("净资产收益率(%)", "加权净资产收益率(%)", "净资产收益率"),
        "net_margin": first("销售净利率(%)", "净利率(%)", "销售净利率"),
        "revenue_growth": first("主营业务收入增长率(%)", "营业收入同比增长率(%)", "营业总收入同比增长率(%)"),
        "profit_growth": first("净利润增长率(%)", "净利润同比增长率(%)", "归属母公司股东的净利润同比增长率(%)"),
        "debt_ratio": first("资产负债率(%)", "资产负债率"),
        "current_ratio": first("流动比率", "流动比率(倍)"),
        "operating_cashflow_per_share": first("每股经营性现金流(元)", "每股经营现金流(元)", "每股经营性现金流"),
        "eps": first("加权每股收益(元)", "基本每股收益(元)", "每股收益(元)"),
    }


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _row_code(row: dict) -> str:
    digits = re.sub(r"\D", "", str(row.get("代码") or row.get("code") or ""))
    return digits[-6:].zfill(6) if digits else ""


def _normalize_quote(row: dict, code: str) -> dict:
    return {
        "code": code,
        "name": str(row.get("名称") or row.get("name") or ""),
        "price": _number(row.get("最新价") if "最新价" in row else row.get("trade")),
        "change_pct": _number(row.get("涨跌幅") if "涨跌幅" in row else row.get("changepercent")),
        "amount": _number(row.get("成交额") if "成交额" in row else row.get("amount")),
        "turnover": _number(row.get("换手率") if "换手率" in row else row.get("turnoverratio")),
        "pe": _number(row.get("市盈率-动态") if "市盈率-动态" in row else row.get("per")),
        "pb": _number(row.get("市净率") if "市净率" in row else row.get("pb")),
        "market_cap": _number(row.get("总市值") if "总市值" in row else row.get("mktcap")),
        "volume_ratio": _number(row.get("量比")),
    }


def _to_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_value(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _to_json_value(value.item())
        except (TypeError, ValueError):
            pass
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
