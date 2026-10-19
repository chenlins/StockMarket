from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from data.fetcher import (
    DataFetchError,
    DataResult,
    StockDataFetcher,
    StockNotFoundError,
    normalize_code,
    resolve_scoring_financials,
)
from data.storage import CacheStorage
from health.report import build_health_payload
from health.screener import TopHealthScreener


ROOT = Path(__file__).resolve().parents[1]


def create_app(config: dict, fetcher=None) -> FastAPI:
    app = FastAPI(title="A 股健康检测工具", version="1.0.0")

    @app.middleware("http")
    async def keep_web_assets_fresh(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response
    storage = None
    if fetcher is None:
        db_path = Path(config["data"]["db_path"])
        if not db_path.is_absolute():
            db_path = ROOT / db_path
        storage = CacheStorage(db_path)
        fetcher = StockDataFetcher(storage, config["data"])
    app.state.fetcher = fetcher
    app.state.config = config
    screening_config = dict(config.get("screening", {}))
    if storage is not None and screening_config.get("enabled", True):
        screening_config["root_config"] = config
        app.state.screener = TopHealthScreener(fetcher, storage, screening_config)
        app.router.add_event_handler("startup", app.state.screener.start)
    else:
        app.state.screener = None

    @app.get("/api/status")
    def status():
        return {"status": "ok", "service": "A 股健康检测工具"}

    @app.get("/api/top-health")
    def top_health():
        if app.state.screener is None:
            return {
                "status": "disabled",
                "progress": {"current": 0, "total": 0},
                "results": [],
                "updated_at": None,
                "message": "自动筛选未启用",
            }
        return app.state.screener.snapshot()

    @app.post("/api/top-health/refresh")
    def refresh_top_health():
        if app.state.screener is None:
            raise HTTPException(status_code=503, detail="自动筛选未启用")
        app.state.screener.start()
        return app.state.screener.snapshot()

    @app.get("/api/top-health/{code}")
    def top_health_report(code: str):
        try:
            normalized = normalize_code(code)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if app.state.screener is None:
            raise HTTPException(status_code=503, detail="自动筛选未启用")
        payload = app.state.screener.report(normalized)
        if payload is None:
            raise HTTPException(status_code=404, detail="该股票不在当前 Top 10 排行快照中")
        return payload

    @app.get("/api/health/{code}")
    def stock_health(code: str):
        try:
            normalized = normalize_code(code)
            quote_result = app.state.fetcher.get_quote(normalized)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except StockNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except DataFetchError as error:
            raise HTTPException(status_code=503, detail=f"暂时无法获取股票行情：{error}") from error

        warnings: list[str] = []
        try:
            financial_result = resolve_scoring_financials(app.state.fetcher, normalized)
            if "回退" in financial_result.source:
                warnings.append("单股财务接口不可用，本次使用同报告期批量财务数据回退计算。")
            if "报告期不同" in financial_result.source:
                warnings.append("批量财务与单股财务报告期不同，本次未跨期拼接指标。")
        except (DataFetchError, RuntimeError, ValueError):
            warnings.append("财务数据暂不可用，相关指标按中性分处理。")
            financial_result = DataResult({}, "不可用", "", False)
        history_result = _optional_data(
            lambda: app.state.fetcher.get_history(normalized, 120),
            [],
            "历史行情暂不可用，趋势和流动性指标按中性分处理。",
            warnings,
        )

        return build_health_payload(
            quote_result,
            financial_result,
            history_result,
            app.state.config,
            warnings,
        )

    app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")
    return app


def _optional_data(operation, default, warning: str, warnings: list[str]) -> DataResult:
    try:
        return operation()
    except (DataFetchError, RuntimeError, ValueError):
        warnings.append(warning)
        return DataResult(default, "不可用", "", False)
