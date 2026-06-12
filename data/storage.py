import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


class DataStorage:
    """SQLite 本地缓存，减少 API 重复请求。"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS stock_list (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_quote (
                    code TEXT,
                    date TEXT,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL,
                    PRIMARY KEY (code, date)
                );
                CREATE TABLE IF NOT EXISTS financial (
                    code TEXT PRIMARY KEY,
                    pe REAL, pb REAL, roe REAL,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS screening_result (
                    run_date TEXT,
                    code TEXT,
                    name TEXT,
                    score REAL,
                    strategy TEXT,
                    details TEXT,
                    PRIMARY KEY (run_date, code)
                );
            """)

    def save_stock_list(self, df: pd.DataFrame):
        now = datetime.now().isoformat()
        records = [(row["code"], row["name"], now) for _, row in df.iterrows()]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO stock_list (code, name, updated_at) VALUES (?, ?, ?)",
                records,
            )

    def get_stock_list(self, max_age_hours: int = 24, allow_stale: bool = False) -> pd.DataFrame | None:
        with self._connect() as conn:
            if allow_stale:
                df = pd.read_sql("SELECT code, name FROM stock_list", conn)
            else:
                cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
                df = pd.read_sql(
                    "SELECT code, name FROM stock_list WHERE updated_at >= ?",
                    conn,
                    params=(cutoff,),
                )
        return df if not df.empty else None

    def save_daily_quotes(self, code: str, df: pd.DataFrame):
        records = [
            (code, row["date"], row["open"], row["high"], row["low"],
             row["close"], row["volume"], row.get("amount", 0))
            for _, row in df.iterrows()
        ]
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO daily_quote
                   (code, date, open, high, low, close, volume, amount)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                records,
            )

    def get_daily_quotes(self, code: str, days: int = 60) -> pd.DataFrame | None:
        with self._connect() as conn:
            df = pd.read_sql(
                """SELECT date, open, high, low, close, volume, amount
                   FROM daily_quote WHERE code = ?
                   ORDER BY date DESC LIMIT ?""",
                conn,
                params=(code, days),
            )
        if df.empty:
            return None
        return df.sort_values("date").reset_index(drop=True)

    def save_financial(self, code: str, pe: float, pb: float, roe: float):
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO financial (code, pe, pb, roe, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (code, pe, pb, roe, now),
            )

    def get_financial(self, code: str, max_age_days: int = 7) -> dict | None:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT pe, pb, roe FROM financial WHERE code = ? AND updated_at >= ?",
                (code, cutoff),
            ).fetchone()
        if row is None:
            return None
        return {"pe": row[0], "pb": row[1], "roe": row[2]}

    def save_screening_results(self, run_date: str, results: list[dict]):
        with self._connect() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO screening_result
                   (run_date, code, name, score, strategy, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (run_date, r["code"], r["name"], r["score"],
                     r.get("strategy", ""), r.get("details", ""))
                    for r in results
                ],
            )

    def get_latest_screening(self) -> pd.DataFrame:
        with self._connect() as conn:
            latest = conn.execute(
                "SELECT MAX(run_date) FROM screening_result"
            ).fetchone()[0]
            if latest is None:
                return pd.DataFrame()
            return pd.read_sql(
                """SELECT code, name, score, strategy, details
                   FROM screening_result WHERE run_date = ?
                   ORDER BY score DESC""",
                conn,
                params=(latest,),
            )
