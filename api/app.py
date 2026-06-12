import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from app_paths import get_config_path, get_web_dir, resolve_db_path
from data.fetcher import DataFetcher, DataFetchError
from data.storage import DataStorage
from strategies.pattern_screener import screen_stocks

WEB_DIR = get_web_dir()


def create_app(config_path: str | None = None) -> FastAPI:
    cfg_path = config_path or str(get_config_path())
    with open(cfg_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["data"]["db_path"] = resolve_db_path(config["data"]["db_path"])

    storage = DataStorage(config["data"]["db_path"])
    fetcher = DataFetcher(storage)
    app = FastAPI(title="GiveMeMoney 选股工具", version="0.2.0")

    @app.get("/api/screen")
    def api_screen(
        minPrice: float | None = Query(None),
        maxPrice: float | None = Query(None),
        minMarketCap: float | None = Query(None),
        maxMarketCap: float | None = Query(None),
        minVolume: float | None = Query(None),
        maxVolume: float | None = Query(None),
        chiNext: str = Query("exclude"),
        star: str = Query("exclude"),
        st: str = Query("exclude"),
        pattern: str = Query("rise_price_volume"),
        maxInspect: int = Query(90),
        sortBy: str = Query("score"),
        limitMinDays: int = Query(5),
        maGapPct: float = Query(5),
        adjustmentPct: float = Query(10),
        forceRefresh: str = Query("false"),
    ):
        query = {
            "minPrice": minPrice,
            "maxPrice": maxPrice,
            "minMarketCap": minMarketCap,
            "maxMarketCap": maxMarketCap,
            "minVolume": minVolume,
            "maxVolume": maxVolume,
            "chiNext": chiNext,
            "star": star,
            "st": st,
            "pattern": pattern,
            "maxInspect": maxInspect,
            "sortBy": sortBy,
            "limitMinDays": limitMinDays,
            "maGapPct": maGapPct,
            "adjustmentPct": adjustmentPct,
            "forceRefresh": forceRefresh,
        }
        try:
            return screen_stocks(fetcher, query)
        except DataFetchError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e) or "screen failed") from e

    @app.get("/api/results")
    def get_results():
        df = storage.get_latest_screening()
        if df.empty:
            return {"message": "暂无选股结果，请先运行 python main.py screen"}
        return df.to_dict(orient="records")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
