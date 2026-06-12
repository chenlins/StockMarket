"""
GiveMeMoney 选股工具 - 命令行入口

用法:
  python main.py screen          # 运行选股
  python main.py screen --export # 选股并导出 CSV
  python main.py backtest 600519 # 对单只股票回测
  python main.py serve           # 启动 Web 服务
  python main.py schedule        # 启动定时任务
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

from app_paths import get_config_path, resolve_db_path
from data.fetcher import DataFetcher, DataFetchError
from data.storage import DataStorage
from strategies.base import ScreeningEngine
from strategies.value import ValueStrategy
from strategies.momentum import MomentumStrategy
from backtest.engine import BacktestEngine


def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else get_config_path()
    with open(cfg_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["data"]["db_path"] = resolve_db_path(config["data"]["db_path"])
    return config


def build_engine(config: dict) -> ScreeningEngine:
    strategies = []
    strat_cfg = config.get("strategies", {})

    if strat_cfg.get("value", {}).get("enabled", True):
        strategies.append(ValueStrategy(strat_cfg["value"]))
    if strat_cfg.get("momentum", {}).get("enabled", True):
        strategies.append(MomentumStrategy(strat_cfg["momentum"]))

    return ScreeningEngine(strategies, config)


def cmd_screen(config: dict, export: bool = False):
    storage = DataStorage(config["data"]["db_path"])
    fetcher = DataFetcher(storage)
    engine = build_engine(config)

    print("正在获取股票列表...")
    try:
        stock_list = fetcher.get_stock_list()
    except DataFetchError as e:
        print(f"错误: {e}")
        sys.exit(1)

    results = engine.run(fetcher, stock_list)
    run_date = datetime.now().strftime("%Y-%m-%d")
    storage.save_screening_results(run_date, results)

    print(f"\n{'='*50}")
    print(f"选股结果 Top {len(results)} ({run_date})")
    print(f"{'='*50}")
    print(f"{'代码':<8} {'名称':<10} {'涨幅':>7} {'得分':>6}  {'策略'}")
    print("-" * 58)
    for r in results:
        pct = r.get("change_pct")
        pct_str = f"{pct:>6.2f}%" if pct is not None else "      -"
        print(f"{r['code']:<8} {r['name']:<10} {pct_str} {r['score']:>6.1f}  {r['strategy']}")

    if export:
        out = Path("output") / f"screening_{run_date}.csv"
        out.parent.mkdir(exist_ok=True)
        import pandas as pd
        pd.DataFrame(results).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n已导出: {out}")


def cmd_backtest(config: dict, code: str):
    storage = DataStorage(config["data"]["db_path"])
    fetcher = DataFetcher(storage)

    print(f"正在获取 {code} 历史数据...")
    history = fetcher.get_daily_history(code, days=250)
    if history.empty:
        print("无法获取历史数据")
        sys.exit(1)

    strat_cfg = config.get("strategies", {}).get("momentum", {})
    engine = BacktestEngine(
        ma_short=strat_cfg.get("ma_short", 5),
        ma_long=strat_cfg.get("ma_long", 20),
    )
    result = engine.run(history)

    print(f"\n回测结果: {code}")
    print(f"  总收益率:   {result.total_return}%")
    print(f"  胜率:       {result.win_rate}%")
    print(f"  最大回撤:   {result.max_drawdown}%")
    print(f"  交易次数:   {result.trade_count}")
    if result.details:
        print("\n  最近 5 笔交易:")
        for t in result.details[-5:]:
            print(f"    {t['date']}  买入 {t['entry']:.2f} → 卖出 {t['exit']:.2f}  盈亏 {t['pnl_pct']}%")


def cmd_serve(config: dict):
    import uvicorn
    from api.app import create_app

    api_cfg = config.get("api", {})
    host = api_cfg.get("host", "127.0.0.1")
    port = api_cfg.get("port", 8000)
    app = create_app()
    print(f"启动服务: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


def cmd_schedule(config: dict):
    from scheduler.jobs import create_scheduler

    sched_cfg = config.get("scheduler", {})
    run_time = sched_cfg.get("run_time", "16:30")

    def job():
        print(f"\n[{datetime.now()}] 定时选股开始")
        cmd_screen(config, export=True)
        print(f"[{datetime.now()}] 定时选股完成")

    scheduler = create_scheduler(job, run_time)
    print(f"定时任务已启动，每个交易日 {run_time} 运行选股 (Ctrl+C 退出)")
    scheduler.start()


def main():
    parser = argparse.ArgumentParser(description="GiveMeMoney 选股工具")
    sub = parser.add_subparsers(dest="command")

    p_screen = sub.add_parser("screen", help="运行选股")
    p_screen.add_argument("--export", action="store_true", help="导出 CSV")

    p_bt = sub.add_parser("backtest", help="单股回测")
    p_bt.add_argument("code", help="股票代码，如 600519")

    sub.add_parser("serve", help="启动 Web 服务")
    sub.add_parser("schedule", help="启动定时选股")

    args = parser.parse_args()
    config = load_config()

    if args.command == "screen":
        cmd_screen(config, export=args.export)
    elif args.command == "backtest":
        cmd_backtest(config, args.code)
    elif args.command == "serve":
        cmd_serve(config)
    elif args.command == "schedule":
        cmd_schedule(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
