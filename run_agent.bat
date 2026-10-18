@echo off
chcp 65001 > nul
title QR Se Print - Windows Print Agent
color 0A
cd /d "%~dp0agent"

echo ======================================================
echo       QR Se Print - Auto Print Desktop Agent
echo ======================================================
echo.

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
echo.
python print_agent.py
echo.
echo ======================================================
echo [!] Agent stopped. Check error above for details.
echo ======================================================
pause
