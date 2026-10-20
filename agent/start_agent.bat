@echo off
chcp 65001 > nul
title QR Se Print - Windows Print Agent
color 0A
cd /d "%~dp0"

echo ======================================================
echo       QR Se Print - Auto Print Desktop Agent
echo ======================================================
echo.
<<<<<<< HEAD

REM Check if Python is available
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python from https://python.org and try again.
    pause
    exit /b 1
)

REM Auto install missing packages
echo [*] Checking required packages...
python -c "import websockets" > nul 2>&1
if errorlevel 1 (
    echo [*] Installing websockets...
    pip install websockets
)

echo [*] Starting agent...
=======
set "QSP_API_KEY="
set /p "QSP_API_KEY=Admin Dashboard থেকে API Key দিন: "
if not defined QSP_API_KEY (
    echo [ERROR] API Key খালি রাখা যাবে না।
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='agent_config.json'; $c=Get-Content -Raw -LiteralPath $p | ConvertFrom-Json; $c.api_key=$env:QSP_API_KEY; $c | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $p"
if errorlevel 1 (
    echo [ERROR] API Key config file-এ save করা যায়নি।
    pause
    exit /b 1
)
echo [OK] API Key এই session-এর জন্য config-এ save হয়েছে।
>>>>>>> 7c8a349 (Add API prompt and Render keep alive job)
echo.
python print_agent.py
echo.
echo ======================================================
echo [!] Agent stopped. Check error above for details.
echo ======================================================
pause
