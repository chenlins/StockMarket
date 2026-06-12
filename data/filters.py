"""A 股股票池过滤：沪深主板、普通股、涨跌幅区间。"""

import re

import pandas as pd

# 沪深主板代码前缀（不含创业板 300、科创板 688、北交所 4/8）
MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")


def normalize_code(code: str) -> str:
    """统一为 6 位数字代码（兼容 sh600519 / sz000001 等格式）。"""
    text = re.sub(r"^(sh|sz|bj)", "", str(code).strip().lower())
    digits = re.sub(r"\D", "", text)
    return digits.zfill(6)[-6:] if digits else text.zfill(6)


def is_main_board(code: str) -> bool:
    return normalize_code(code).startswith(MAIN_BOARD_PREFIXES)


def is_ordinary_stock(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    upper = name.upper()
    if "ST" in upper or "退" in name:
        return False
    return True


def apply_universe_filters(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """按配置过滤股票池，在策略评分前缩小分析范围。"""
    result = df.copy()
    result["code"] = result["code"].apply(normalize_code)

    if config.get("main_board_only", True):
        result = result[result["code"].apply(is_main_board)]

    if config.get("ordinary_only", True) or config.get("exclude_st", True):
        result = result[result["name"].apply(is_ordinary_stock)]

    pct_min = config.get("change_pct_min", 0)
    pct_max = config.get("change_pct_max", 10)
    if "change_pct" in result.columns and result["change_pct"].notna().any():
        pct = pd.to_numeric(result["change_pct"], errors="coerce")
        result = result[pct.notna() & pct.between(pct_min, pct_max, inclusive="both")]

    if "price" in result.columns and result["price"].notna().any():
        min_price = config.get("min_price", 3.0)
        result = result[result["price"].fillna(0) >= min_price]

    return result.reset_index(drop=True)


def describe_universe(config: dict) -> str:
    parts = []
    if config.get("main_board_only", True):
        parts.append("沪深主板")
    if config.get("ordinary_only", True):
        parts.append("普通股")
    pct_min = config.get("change_pct_min", 0)
    pct_max = config.get("change_pct_max", 10)
    parts.append(f"涨幅 {pct_min}%-{pct_max}%")
    return " · ".join(parts)
