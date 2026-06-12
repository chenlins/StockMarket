"""应用路径解析，兼容开发环境与 PyInstaller 打包后的 exe。"""

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    """可写数据目录：开发时为项目根目录，打包后为 exe 所在目录。"""
    if is_frozen():
        return Path(sys.executable).parent.resolve()
    return Path(__file__).resolve().parent


def bundle_root() -> Path:
    """只读资源目录：开发时为项目根目录，打包后为 PyInstaller 解压目录。"""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def get_config_path() -> Path:
    local = app_root() / "config.yaml"
    if local.exists():
        return local
    return bundle_root() / "config.yaml"


def get_web_dir() -> Path:
    return bundle_root() / "web"


def resolve_db_path(configured: str) -> str:
    path = Path(configured)
    if path.is_absolute():
        return configured
    return str(app_root() / path)
