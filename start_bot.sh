#!/bin/bash
# Modular Options Trading Bot - Mac/Linux Startup Script
# This script starts both the OpenBB API server and the modular trading bot

echo "================================================================================"
echo "DUBK Options Bot - Startup"
echo "================================================================================"
echo ""

# Check if virtual environment exists
if [ ! -f "geotp_env/bin/activate" ]; then
    echo "[ERROR] Virtual environment not found!"
    echo "Please run: python3 -m venv venv"
    echo "Then install dependencies: pip3 install -r requirements_openbb.txt"
    exit 1
fi

# Check if .env file exists in home directory
if [ ! -f ".env" ]; then
    echo "[ERROR] .env file not found in home directory!"
    echo "The .env file should exist in ~/ (your home directory)"
    echo "It contains your API keys and is NOT transferred during deployment"
    exit 1
fi
echo "[*] Found .env file with API keys"

echo "[*] Activating virtual environment..."
source geotp_env/bin/activate

echo "[*] Starting OpenBB REST API server in background..."
nohup python -m uvicorn openbb_core.api.rest_api:app --host 127.0.0.1 --port 6900 > openbb_api.log 2>&1 &
API_PID=$!
echo "[*] OpenBB API server started (PID: $API_PID)"

echo "[*] Waiting for API server to start (3 seconds)..."
sleep 3

python3 run_bot.py

echo ""
echo "Bot stopped. Stopping API server..."
kill $API_PID 2>/dev/null
echo "Done."
