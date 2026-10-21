from __future__ import annotations

import copy
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from data.fetcher import DataFetchError, DataResult, resolve_scoring_financials
from health.engine import calculate_health
from health.report import build_health_payload
from health.trade_plan import ensure_trade_plan


class TopHealthScreener:
    """Score the market's hottest stocks and return a strict health Top 10."""

    CACHE_KEY = "screening:hot500-top-health:v6"
    LEGACY_CACHE_KEYS = ("screening:hot200-top-health:v5",)

    def __init__(self, fetcher, storage, config: dict):
        self.fetcher = fetcher
        self.storage = storage
        self.config = config
        self._lock = threading.Lock()
        self._running = False
        self._state = {
            "status": "idle",
            "progress": {"current": 0, "total": 0},
            "results": [],
            "reports": {},
            "updated_at": None,
            "message": "等待启动筛选",
        }
        cached = storage.get(self.CACHE_KEY)
        if not (cached and isinstance(cached["payload"], dict)):
            for legacy_key in self.LEGACY_CACHE_KEYS:
                cached = storage.get(legacy_key)
                if cached and isinstance(cached["payload"], dict):
                    break
        if cached and isinstance(cached["payload"], dict):
            self._state.update(cached["payload"])
            self._state["status"] = "cached"
            self._state["message"] = "正在后台刷新，当前展示上次结果"
            self._repair_stored_trade_plans()

    def start(self) -> None:
        if not self.config.get("enabled", True):
            return
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state["status"] = "running"
            hot_limit = int(self.config.get("hot_limit", 500))
            self._state["message"] = (
                "正在后台刷新，当前展示上次排行"
                if self._state["results"]
                else f"正在获取市场热度前 {hot_limit} 股票"
            )
        threading.Thread(target=self._refresh, name="top-health-screen", daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                key: copy.deepcopy(value)
                for key, value in self._state.items()
                if key != "reports"
            }

    def report(self, code: str) -> dict | None:
        with self._lock:
            payload = self._state.get("reports", {}).get(code)
            result = copy.deepcopy(payload) if payload is not None else None
            if result is not None:
                ensure_trade_plan(result)
                if self._running:
                    result.setdefault("snapshot", {})["refresh_in_progress"] = True
            return result

    def _repair_stored_trade_plans(self) -> None:
        reports = self._state.get("reports") or {}
        changed = False
        for payload in reports.values():
            if isinstance(payload, dict) and ensure_trade_plan(payload):
                changed = True
        if changed:
            self.storage.put(self.CACHE_KEY, copy.deepcopy(self._state), "启动健康排行")

    def _refresh(self) -> None:
        try:
            quote_result = self.fetcher.get_quote_list()
            eligible = [quote for quote in quote_result.data if _is_eligible(quote)]
            quote_by_code = {quote["code"]: quote for quote in eligible}
            hot_limit = int(self.config.get("hot_limit", 500))
            hot_result = self.fetcher.get_hot_rank(hot_limit)
            hot_rows = list(hot_result.data)

            seen = {row["code"] for row in hot_rows}
            if len(hot_rows) < hot_limit:
                active_quotes = sorted(
                    (quote for quote in eligible if quote["code"] not in seen),
                    key=lambda quote: _number(quote.get("amount")) or 0,
                    reverse=True,
                )
                for quote in active_quotes[: hot_limit - len(hot_rows)]:
                    hot_rows.append(
                        {
                            "code": quote["code"],
                            "heat_rank": len(hot_rows) + 1,
                            "source_rank": len(hot_rows) + 1,
                            "heat_list": "成交活跃补位",
                        }
                    )

            market_financials: DataResult | None = None
            financials_by_code = {}
            financial_source = "不可用"
            market_financials_failed = False
            try:
                market_financials = self.fetcher.get_market_financials()
                financials_by_code = market_financials.data.get("stocks", {})
                financial_source = market_financials.source
            except DataFetchError:
                market_financials_failed = True

            candidates = []
            for hot in hot_rows:
                quote = quote_by_code.get(hot["code"])
                if quote is None:
                    continue
                financials = financials_by_code.get(quote["code"], {})
                enriched_quote = _enrich_quote(quote, financials)
                if not _is_eligible(enriched_quote):
                    continue
                enriched_quote["heat_rank"] = hot["heat_rank"]
                enriched_quote["heat_list"] = hot["heat_list"]
                candidates.append((enriched_quote, financials))

            top_count = int(self.config.get("top_count", 10))
            total = len(candidates)
            self._update(
                progress={"current": 0, "total": total},
                message=f"已获取热度候选，正在完整检测 {total} 只股票",
            )

            scored = []
            completed = 0
            initial_failures = 0

            def score_candidate(candidate):
                quote, financials = candidate
                code = quote["code"]
                history_result = DataResult([], "不可用", "")
                try:
                    history_result = self.fetcher.get_history(code, 120)
                except (DataFetchError, ValueError):
                    pass
                report = calculate_health(
                    quote,
                    financials,
                    history_result.data,
                    self.config["root_config"],
                )
                return {
                    "quote": quote,
                    "history_result": history_result,
                    "compact": _compact_result(quote, report),
                }

            workers = max(1, min(8, int(self.config.get("workers", 6))))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(score_candidate, candidate): candidate for candidate in candidates}
                for future in as_completed(futures):
                    completed += 1
                    quote = futures[future][0]
                    try:
                        scored.append(future.result())
                    except Exception:
                        initial_failures += 1
                    self._update(
                        progress={"current": completed, "total": total},
                        message=f"正在批量初筛 {quote['name']}（{quote['code']}） {completed}/{total}",
                    )

            if not scored:
                raise DataFetchError("所有候选股票初筛均失败")
            precise_count = min(
                len(scored),
                (
                    len(scored)
                    if market_financials_failed
                    else max(top_count, int(self.config.get("precise_count", 30)))
                ),
            )
            preliminary = sorted(
                scored,
                key=lambda item: (-item["compact"]["score"], item["compact"]["code"]),
            )[:precise_count]
            precise_scored = []
            precise_total = total + precise_count
            self._update(
                progress={"current": total, "total": precise_total},
                message=f"初筛完成，正在用单股完整财务数据精确复评前 {precise_count} 名",
            )

            def rescore_candidate(entry):
                quote = entry["quote"]
                code = quote["code"]
                warnings = []
                try:
                    financial_result = resolve_scoring_financials(
                        self.fetcher,
                        code,
                        market_financials,
                    )
                except (DataFetchError, RuntimeError, ValueError):
                    financial_result = DataResult({}, "不可用", "")
                    warnings.append("财务数据暂不可用，相关指标按中性分处理。")
                if "回退" in financial_result.source:
                    warnings.append("单股财务接口不可用，本报告使用同报告期批量财务数据回退计算。")
                if "报告期不同" in financial_result.source:
                    warnings.append("批量财务与单股财务报告期不同，本次未跨期拼接指标。")
                precise_quote = _enrich_quote(quote, financial_result.data)
                quote_for_report = DataResult(
                    precise_quote,
                    quote_result.source,
                    quote_result.fetched_at,
                    quote_result.stale,
                )
                payload = build_health_payload(
                    quote_for_report,
                    financial_result,
                    entry["history_result"],
                    self.config["root_config"],
                    warnings,
                )
                return {
                    "compact": _compact_result(precise_quote, payload),
                    "payload": payload,
                }

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(rescore_candidate, entry): entry for entry in preliminary}
                for future in as_completed(futures):
                    completed += 1
                    quote = futures[future]["quote"]
                    try:
                        precise_scored.append(future.result())
                    except Exception:
                        initial_failures += 1
                    self._update(
                        progress={"current": completed, "total": precise_total},
                        message=f"正在精确复评 {quote['name']}（{quote['code']}） {completed - total}/{precise_count}",
                    )

            if not precise_scored:
                raise DataFetchError("所有候选股票精确复评均失败")
            ordered = sorted(
                precise_scored,
                key=lambda item: (-item["compact"]["score"], item["compact"]["code"]),
            )
            min_confidence = int(self.config.get("min_confidence", 60))
            reliable = [
                item
                for item in ordered
                if item["compact"]["confidence"]["score"] >= min_confidence
            ]
            selected = _select_reliable_first(ordered, top_count, min_confidence)
            updated_at = datetime.now(timezone.utc).isoformat()
            results = []
            reports = {}
            for item in selected:
                compact = item["compact"]
                code = compact["code"]
                item["payload"]["snapshot"] = {
                    "kind": "ranking",
                    "updated_at": updated_at,
                    "score": compact["score"],
                }
                results.append(compact)
                reports[code] = item["payload"]

            payload = {
                "status": "ready",
                "progress": {"current": precise_total, "total": precise_total},
                "results": results,
                "reports": reports,
                "updated_at": updated_at,
                "message": _screening_message(
                    len(hot_rows),
                    total,
                    precise_count,
                    len(results),
                    len(reliable),
                    market_financials_failed,
                    initial_failures,
                ),
                "universe_count": len(eligible),
                "hot_pool_count": len(hot_rows),
                "market_scored_count": total,
                "candidate_count": precise_count,
                "quote_source": quote_result.source,
                "financial_source": financial_source,
                "heat_source": hot_result.source,
            }
            self.storage.put(self.CACHE_KEY, payload, "启动健康排行")
            self._update(**payload)
        except Exception as error:
            self._update(
                status="error",
                message=f"本次自动筛选失败：{error}",
            )
        finally:
            with self._lock:
                self._running = False

    def _update(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)


def _screening_message(
    hot_count: int,
    initial_count: int,
    precise_count: int,
    result_count: int,
    reliable_count: int,
    market_financials_failed: bool,
    failure_count: int,
) -> str:
    message = (
        f"热度池 {hot_count} 只，初筛 {initial_count} 只并精确复评 {precise_count} 只，"
        f"已选出综合健康分 Top {result_count}"
    )
    if reliable_count < result_count:
        message += f"（{reliable_count} 只达到可信度门槛，其余为补位）"
    if market_financials_failed:
        message += "；批量财务不可用，已对全部候选执行单股精评"
    if failure_count:
        message += f"；{failure_count} 次单股任务失败并已跳过"
    return message


def _select_reliable_first(ordered: list[dict], count: int, min_confidence: int) -> list[dict]:
    reliable = [
        item
        for item in ordered
        if item["compact"]["confidence"]["score"] >= min_confidence
    ]
    selected = reliable[:count]
    if len(selected) < count:
        selected_codes = {item["compact"]["code"] for item in selected}
        selected.extend(
            item
            for item in ordered
            if item["compact"]["code"] not in selected_codes
        )
    return selected[:count]


def _is_eligible(quote: dict) -> bool:
    name = str(quote.get("name") or "").upper()
    code = str(quote.get("code") or "")
    price = _number(quote.get("price"))
    amount = _number(quote.get("amount"))
    pe = _number(quote.get("pe"))
    pb = _number(quote.get("pb"))
    return (
        code.startswith(("0", "3", "6", "8", "4"))
        and price is not None
        and price > 0
        and amount is not None
        and amount >= 50_000_000
        and (pe is None or 0 < pe <= 80)
        and (pb is None or 0 < pb <= 10)
        and "ST" not in name
        and "退" not in name
    )


def _enrich_quote(quote: dict, financials: dict) -> dict:
    enriched = dict(quote)
    price = _number(enriched.get("price"))
    if price is not None and _number(enriched.get("pe")) is None:
        annualized_eps = _number(financials.get("annualized_eps"))
        if annualized_eps is not None and annualized_eps > 0:
            enriched["pe"] = price / annualized_eps
    if price is not None and _number(enriched.get("pb")) is None:
        book_value = _number(financials.get("book_value_per_share"))
        if book_value is not None and book_value > 0:
            enriched["pb"] = price / book_value
    return enriched


def _compact_result(quote: dict, report: dict) -> dict:
    best = max(report["dimensions"], key=lambda item: item["percentage"])
    weakest = min(report["dimensions"], key=lambda item: item["percentage"])
    return {
        "code": quote["code"],
        "name": quote["name"],
        "score": report["score"],
        "grade": report["grade"],
        "confidence": report["confidence"],
        "price": quote.get("price"),
        "change_pct": quote.get("change_pct"),
        "heat_rank": quote.get("heat_rank"),
        "heat_list": quote.get("heat_list"),
        "best_dimension": best["name"],
        "weakest_dimension": weakest["name"],
    }


def _number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None
