@echo off
title QR Se Print - Local Print Agent
color 0A
echo ======================================================
echo           Starting QR Se Print Agent...
echo ======================================================
cd /d "%~dp0"
python print_agent.py
pause

