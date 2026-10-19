from __future__ import annotations

from data.fetcher import DataResult

from .engine import calculate_health


DISCLAIMER = "本结果仅供学习研究，不构成投资建议。请结合原始财报、公告与自身风险承受能力判断。"


def build_health_payload(
    quote_result: DataResult,
    financial_result: DataResult,
    history_result: DataResult,
    config: dict,
    warnings: list[str] | None = None,
    snapshot: dict | None = None,
) -> dict:
    """Build the canonical API/report payload from one coherent data snapshot."""
    report = calculate_health(
        quote_result.data,
        financial_result.data,
        history_result.data,
        config,
    )
    sources = {
        "quote": quote_result.meta(),
        "financials": financial_result.meta(),
        "history": history_result.meta(),
    }
    result_warnings = list(warnings or [])
    if any(item["stale"] for item in sources.values()):
        result_warnings.append("部分实时接口不可用，本次使用了缓存数据，请留意数据时间。")

    payload = {
        "stock": quote_result.data,
        **report,
        "raw_metrics": {
            "financials": financial_result.data,
            "technical": report["calculated_metrics"],
        },
        "data_sources": sources,
        "warnings": result_warnings,
        "disclaimer": DISCLAIMER,
    }
    if snapshot is not None:
        payload["snapshot"] = snapshot
    return payload
