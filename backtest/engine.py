from dataclasses import dataclass

import pandas as pd

from indicators.technical import add_ma


@dataclass
class BacktestResult:
    total_return: float
    win_rate: float
    max_drawdown: float
    trade_count: int
    details: list[dict]


class BacktestEngine:
    """
    简单回测：均线金叉买入、死叉卖出。
    用于验证技术策略思路，非完整量化框架。
    """

    def __init__(self, ma_short: int = 5, ma_long: int = 20):
        self.ma_short = ma_short
        self.ma_long = ma_long

    def run(self, history: pd.DataFrame, initial_capital: float = 100000) -> BacktestResult:
        if history.empty or len(history) < self.ma_long + 5:
            return BacktestResult(0, 0, 0, 0, [])

        df = add_ma(history, [self.ma_short, self.ma_long]).dropna().reset_index(drop=True)
        capital = initial_capital
        shares = 0
        peak = capital
        max_dd = 0.0
        trades = []
        entry_price = 0.0

        for i in range(1, len(df)):
            prev = df.iloc[i - 1]
            curr = df.iloc[i]
            price = curr["close"]

            golden_cross = (
                prev[f"ma{self.ma_short}"] <= prev[f"ma{self.ma_long}"]
                and curr[f"ma{self.ma_short}"] > curr[f"ma{self.ma_long}"]
            )
            death_cross = (
                prev[f"ma{self.ma_short}"] >= prev[f"ma{self.ma_long}"]
                and curr[f"ma{self.ma_short}"] < curr[f"ma{self.ma_long}"]
            )

            if golden_cross and shares == 0:
                shares = int(capital / price)
                entry_price = price
                capital -= shares * price

            elif death_cross and shares > 0:
                pnl = (price - entry_price) / entry_price
                capital += shares * price
                trades.append({
                    "entry": entry_price,
                    "exit": price,
                    "pnl_pct": round(pnl * 100, 2),
                    "date": curr["date"],
                })
                shares = 0

            portfolio = capital + shares * price
            peak = max(peak, portfolio)
            dd = (peak - portfolio) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        final_value = capital + shares * df.iloc[-1]["close"]
        total_return = (final_value - initial_capital) / initial_capital
        wins = sum(1 for t in trades if t["pnl_pct"] > 0)
        win_rate = wins / len(trades) if trades else 0

        return BacktestResult(
            total_return=round(total_return * 100, 2),
            win_rate=round(win_rate * 100, 2),
            max_drawdown=round(max_dd * 100, 2),
            trade_count=len(trades),
            details=trades,
        )
