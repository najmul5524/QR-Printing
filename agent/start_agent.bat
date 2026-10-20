@echo off
setlocal EnableExtensions
chcp 65001 >nul
title QR Se Print - Windows Print Agent
color 0A
cd /d "%~dp0"

echo ======================================================
echo       QR Se Print - Auto Print Desktop Agent
echo ======================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo Install Python and enable "Add Python to PATH".
    pause
    exit /b 1
)

set "QSP_API_KEY="
set /p "QSP_API_KEY=Enter your Admin Dashboard API Key: "
if not defined QSP_API_KEY (
    echo [ERROR] API Key cannot be empty.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p='agent_config.json'; $c=Get-Content -Raw -LiteralPath $p | ConvertFrom-Json; $c.api_key=$env:QSP_API_KEY; $c | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $p"
if errorlevel 1 (
    echo [ERROR] Could not save API Key to agent_config.json.
    pause
    exit /b 1
)

echo [OK] API Key saved. Starting agent...
echo.
python print_agent.py

echo.
echo ======================================================
echo Agent stopped. Check the error above.
echo ======================================================
pause
endlocal
