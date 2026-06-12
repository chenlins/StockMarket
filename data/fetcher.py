import time
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from .filters import normalize_code
from .storage import DataStorage


class DataFetchError(Exception):
    """数据获取失败。"""


class DataFetcher:
    """通过 AkShare 获取 A 股数据，带本地缓存、重试与多数据源回退。"""

    def __init__(self, storage: DataStorage):
        self.storage = storage
        self._last_request = 0.0
        self._min_interval = 0.3
        self._last_source = ""

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _retry(self, func, retries: int = 3, delay: float = 2.0):
        last_err = None
        for attempt in range(retries):
            try:
                self._throttle()
                return func()
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(delay * (attempt + 1))
        raise last_err

    @property
    def last_source(self) -> str:
        return self._last_source

    def get_stock_list(self, with_spot: bool = True) -> pd.DataFrame:
        cached = self.storage.get_stock_list()
        if cached is not None:
            self._last_source = "cache"
            df = self._enrich_stock_list(cached)
        else:
            df = self._fetch_stock_list_fresh()

        df = self._normalize_df_codes(df)
        if with_spot and df["change_pct"].isna().all():
            df = self._merge_spot_quotes(df)
        return df

    def _fetch_stock_list_fresh(self) -> pd.DataFrame:
        sources = [
            ("东方财富", self._fetch_from_eastmoney),
            ("新浪财经", self._fetch_from_sina),
            ("交易所基础列表", self._fetch_from_basic),
        ]

        errors = []
        for name, fetcher in sources:
            try:
                df = self._retry(fetcher, retries=2, delay=1.5)
                if df is not None and not df.empty:
                    self._last_source = name
                    self.storage.save_stock_list(df[["code", "name"]])
                    print(f"  数据源: {name} ({len(df)} 只)")
                    if df["change_pct"].isna().all():
                        df = self._merge_spot_quotes(df)
                    return df
            except Exception as e:
                errors.append(f"{name}: {e}")

        stale = self.storage.get_stock_list(allow_stale=True)
        if stale is not None:
            self._last_source = "过期缓存"
            print("  警告: 网络请求失败，使用本地过期缓存")
            df = self._enrich_stock_list(stale)
            return self._merge_spot_quotes(df)

        raise DataFetchError(
            "无法获取股票列表，请检查网络后重试。\n"
            + "\n".join(f"  - {err}" for err in errors)
        )

    def _merge_spot_quotes(self, df: pd.DataFrame) -> pd.DataFrame:
        """合并实时涨跌幅，供涨幅过滤使用。"""
        base = df.drop(
            columns=["price", "pe", "pb", "volume", "amount", "change_pct", "turnover", "market_cap"],
            errors="ignore",
        )
        spot_sources = [
            ("新浪财经", self._fetch_from_sina),
            ("东方财富", self._fetch_from_eastmoney),
        ]
        for name, fetch in spot_sources:
            try:
                spot = self._retry(fetch, retries=2, delay=1.0)
                if spot is None or spot.empty:
                    continue
                spot = spot.drop_duplicates(subset="code", keep="first")
                spot_cols = spot[
                    ["code", "price", "pe", "pb", "volume", "amount", "change_pct", "turnover", "market_cap"]
                ]
                result = base.merge(spot_cols, on="code", how="left")
                if result["change_pct"].notna().any():
                    self._last_source = f"{self._last_source}+{name}行情"
                    return result
            except Exception:
                continue
        print("  警告: 无法获取涨跌幅，涨幅过滤将被跳过")
        return df

    def _enrich_stock_list(self, df: pd.DataFrame) -> pd.DataFrame:
        """为仅含 code/name 的缓存补充可选行情字段。"""
        result = df.copy()
        for col in ("price", "pe", "pb", "volume", "amount", "change_pct", "turnover", "market_cap"):
            if col not in result.columns:
                result[col] = pd.NA
        return result

    def _normalize_df_codes(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result["code"] = result["code"].apply(normalize_code)
        return result

    def _fetch_from_eastmoney(self) -> pd.DataFrame:
        raw = ak.stock_zh_a_spot_em()
        df = pd.DataFrame({
            "code": raw["代码"].astype(str),
            "name": raw["名称"],
            "price": pd.to_numeric(raw["最新价"], errors="coerce"),
            "pe": pd.to_numeric(raw["市盈率-动态"], errors="coerce"),
            "pb": pd.to_numeric(raw["市净率"], errors="coerce"),
            "volume": pd.to_numeric(raw["成交量"], errors="coerce"),
            "amount": pd.to_numeric(raw["成交额"], errors="coerce"),
            "change_pct": pd.to_numeric(raw["涨跌幅"], errors="coerce"),
            "turnover": pd.to_numeric(raw.get("换手率"), errors="coerce"),
            "market_cap": pd.to_numeric(raw.get("总市值"), errors="coerce"),
        })
        return self._normalize_df_codes(df)

    def _fetch_from_sina(self) -> pd.DataFrame:
        raw = ak.stock_zh_a_spot()
        df = pd.DataFrame({
            "code": raw["代码"].astype(str),
            "name": raw["名称"],
            "price": pd.to_numeric(raw["最新价"], errors="coerce"),
            "pe": pd.NA,
            "pb": pd.NA,
            "volume": pd.to_numeric(raw["成交量"], errors="coerce"),
            "amount": pd.to_numeric(raw.get("成交额"), errors="coerce"),
            "change_pct": pd.to_numeric(raw["涨跌幅"], errors="coerce"),
            "turnover": pd.to_numeric(raw.get("换手率"), errors="coerce"),
            "market_cap": pd.NA,
        })
        return self._normalize_df_codes(df)

    def _fetch_from_basic(self) -> pd.DataFrame:
        raw = ak.stock_info_a_code_name()
        df = pd.DataFrame({
            "code": raw["code"].astype(str),
            "name": raw["name"],
            "price": pd.NA,
            "pe": pd.NA,
            "pb": pd.NA,
            "volume": pd.NA,
            "amount": pd.NA,
            "change_pct": pd.NA,
        })
        return self._normalize_df_codes(df)

    def get_daily_history(self, code: str, days: int = 60) -> pd.DataFrame:
        cached = self.storage.get_daily_quotes(code, days)
        if cached is not None and len(cached) >= days * 0.8:
            return cached

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")

        def _fetch():
            return ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )

        try:
            raw = self._retry(_fetch, retries=2, delay=1.0)
        except Exception:
            return cached if cached is not None else pd.DataFrame()

        if raw.empty:
            return cached if cached is not None else pd.DataFrame()

        df = pd.DataFrame({
            "date": raw["日期"].astype(str),
            "open": pd.to_numeric(raw["开盘"], errors="coerce"),
            "high": pd.to_numeric(raw["最高"], errors="coerce"),
            "low": pd.to_numeric(raw["最低"], errors="coerce"),
            "close": pd.to_numeric(raw["收盘"], errors="coerce"),
            "volume": pd.to_numeric(raw["成交量"], errors="coerce"),
            "amount": pd.to_numeric(raw["成交额"], errors="coerce"),
        }).dropna()

        self.storage.save_daily_quotes(code, df)
        return df.tail(days).reset_index(drop=True)

    def get_financial_indicators(self, code: str) -> dict | None:
        cached = self.storage.get_financial(code)
        if cached is not None:
            return cached

        def _fetch():
            return ak.stock_financial_analysis_indicator(symbol=code)

        try:
            raw = self._retry(_fetch, retries=2, delay=1.0)
        except Exception:
            return None

        if raw.empty:
            return None

        latest = raw.iloc[-1]
        result = {
            "pe": _safe_float(latest.get("市盈率")),
            "pb": _safe_float(latest.get("市净率")),
            "roe": _safe_float(latest.get("净资产收益率(%)")),
        }
        self.storage.save_financial(code, result["pe"], result["pb"], result["roe"])
        return result


def _safe_float(val) -> float:
    try:
        v = float(val)
        return v if pd.notna(v) else 0.0
    except (TypeError, ValueError):
        return 0.0
