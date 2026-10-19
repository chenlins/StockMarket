from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn
import yaml

from api.app import create_app


ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def main() -> None:
    parser = argparse.ArgumentParser(description="A 股健康检测工具")
    parser.add_argument("--host", help="监听地址")
    parser.add_argument("--port", type=int, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    config = load_config()
    host = args.host or config["server"]["host"]
    port = args.port or int(config["server"]["port"])
    if not args.no_browser:
        browser_host = "127.0.0.1" if host == "0.0.0.0" else host
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{browser_host}:{port}")).start()

    uvicorn.run(create_app(config), host=host, port=port)


app = create_app(load_config())


if __name__ == "__main__":
    main()
