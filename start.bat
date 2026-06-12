@echo off
chcp 65001 >nul
cd /d "%~dp0"
title GiveMeMoney 选股工具

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH
    echo 或直接双击 GiveMeMoney.exe 免安装启动
    pause
    exit /b 1
)

echo ========================================
echo   GiveMeMoney 选股工具
echo ========================================
echo   正在启动 Web 服务...
echo   浏览器将自动打开 http://127.0.0.1:8000
echo   关闭此窗口即可停止服务
echo ========================================

start "" "http://127.0.0.1:8000"
python main.py serve
if errorlevel 1 (
    echo.
    echo [错误] 启动失败，请确认已安装依赖: pip install -r requirements.txt
    pause
)
