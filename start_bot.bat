@echo off
REM OpenBB Options Trading Bot - Windows Startup Script
REM This script starts both the OpenBB API server and the trading bot

echo ================================================================================
echo OpenBB Options Trading Bot - Startup
echo ================================================================================
echo.

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found!
    echo Please run: python -m venv venv
    echo Then install dependencies: pip install -r requirements_openbb.txt
    pause
    exit /b 1
)

REM Check if .env file exists
if not exist ".env" (
    echo [ERROR] .env file not found!
    echo Please create .env file with your API keys.
    echo See .env.example for template.
    pause
    exit /b 1
)

echo [*] Activating virtual environment...
call venv\Scripts\activate.bat

echo [*] Starting OpenBB REST API server in background...
start "OpenBB API Server" /MIN cmd /k "venv\Scripts\activate.bat && python -m uvicorn openbb_core.api.rest_api:app --host 127.0.0.1 --port 6900"

echo [*] Waiting for API server to start (10 seconds)...
timeout /t 10 /nobreak >nul

echo [*] Starting Options Trading Bot...
echo.
python openbb_options_bot.py

echo.
echo Bot stopped. Press any key to exit...
pause >nul
