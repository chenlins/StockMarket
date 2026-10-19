@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PORT=8788"
for /f "tokens=2" %%P in ('findstr /C:"  port:" "%~dp0config.yaml"') do set "PORT=%%P"

echo [restart] 正在关闭占用 %PORT% 端口的已有服务...
set "KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /I "LISTENING" ^| findstr /C:":%PORT% "') do (
    if not "%%P"=="0" (
        echo [restart] 结束进程 PID %%P
        taskkill /F /T /PID %%P >nul 2>&1
        set "KILLED=1"
    )
)

if "!KILLED!"=="1" echo [restart] 等待端口释放...

set /a TRIES=0
:waitport
netstat -ano | findstr /I "LISTENING" | findstr /C:":%PORT% " >nul
if errorlevel 1 goto startserver
set /a TRIES+=1
if !TRIES! GEQ 15 (
    echo [restart] 端口 %PORT% 仍被占用，已取消启动。
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto waitport

:startserver
echo [restart] 启动服务 http://127.0.0.1:%PORT%
python main.py
set "EXITCODE=!ERRORLEVEL!"
if not "!EXITCODE!"=="0" (
    echo.
    echo [restart] 服务退出，代码 !EXITCODE!
    pause
    exit /b !EXITCODE!
)
endlocal
