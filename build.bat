@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 打包 GiveMeMoney.exe

where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，无法打包
    pause
    exit /b 1
)

echo 正在安装/检查依赖...
pip install -r requirements.txt pyinstaller -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo 正在打包 GiveMeMoney.exe，请稍候...
pyinstaller --noconfirm GiveMeMoney.spec
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

copy /Y "dist\GiveMeMoney.exe" "GiveMeMoney.exe" >nul
echo.
echo 打包完成: %~dp0GiveMeMoney.exe
echo 可将 GiveMeMoney.exe 单独复制到任意目录运行，无需安装 Python。
pause
