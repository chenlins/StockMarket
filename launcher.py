"""GiveMeMoney 图形启动入口，供 PyInstaller 打包为独立 exe。"""

import os
import sys
import threading
import time
import webbrowser

from app_paths import app_root, is_frozen


def _prepare_runtime() -> None:
    if is_frozen():
        os.chdir(app_root())


def main() -> None:
    _prepare_runtime()

    from main import cmd_serve, load_config

    config = load_config()
    api_cfg = config.get("api", {})
    host = api_cfg.get("host", "127.0.0.1")
    port = api_cfg.get("port", 8000)
    url = f"http://{host}:{port}"

    def open_browser() -> None:
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print("=" * 50)
    print("  GiveMeMoney 选股工具")
    print("=" * 50)
    print(f"  访问地址: {url}")
    print("  关闭此窗口即可停止服务")
    print("=" * 50)

    cmd_serve(config)


if __name__ == "__main__":
    main()
