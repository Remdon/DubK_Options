@echo off

echo ===========================================
echo Deploying DUBK Options Trading Bot to EC2
echo ===========================================
echo.

REM Set variables
set KEY="C:\Users\kyler\Downloads\Trading.ppk"
set HOST=ubuntu@ec2-3-101-103-242.us-west-1.compute.amazonaws.com
set LOCAL_PATH=C:\Users\kyler\Documents\AI_BOT

REM Create directory structure on EC2 first
echo [*] Creating directory structure on EC2...
plink -i %KEY% %HOST% "mkdir -p config src/core src/utils src/risk src/connectors src/test src/analyzers src/strategies src/scanners src/order_management logs"
echo.

REM Transfer main files
echo [*] Transferring main entry point...
pscp -i %KEY% "%LOCAL_PATH%\run_bot.py" %HOST%:run_bot.py
pscp -i %KEY% "%LOCAL_PATH%\requirements_openbb.txt" %HOST%:requirements_openbb.txt
echo.

REM Transfer config directory
echo [*] Transferring configuration module...
pscp -i %KEY% "%LOCAL_PATH%\config\__init__.py" %HOST%:config/
pscp -i %KEY% "%LOCAL_PATH%\config\default_config.py" %HOST%:config/
echo.

REM Transfer src directory root
echo [*] Transferring src module root...
pscp -i %KEY% "%LOCAL_PATH%\src\__init__.py" %HOST%:src/
pscp -i %KEY% "%LOCAL_PATH%\src\bot.py" %HOST%:src/
echo.

REM Transfer src/core module
echo [*] Transferring core module...
pscp -i %KEY% "%LOCAL_PATH%\src\core\__init__.py" %HOST%:src/core/
pscp -i %KEY% "%LOCAL_PATH%\src\core\trade_journal.py" %HOST%:src/core/
pscp -i %KEY% "%LOCAL_PATH%\src\core\alert_manager.py" %HOST%:src/core/
pscp -i %KEY% "%LOCAL_PATH%\src\core\market_calendar.py" %HOST%:src/core/
pscp -i %KEY% "%LOCAL_PATH%\src\core\scan_result_cache.py" %HOST%:src/core/
pscp -i %KEY% "%LOCAL_PATH%\src\core\colors.py" %HOST%:src/core/
echo.

REM Transfer src/utils module
echo [*] Transferring utils module...
pscp -i %KEY% "%LOCAL_PATH%\src\utils\__init__.py" %HOST%:src/utils/
pscp -i %KEY% "%LOCAL_PATH%\src\utils\validators.py" %HOST%:src/utils/
pscp -i %KEY% "%LOCAL_PATH%\src\utils\circuit_breaker.py" %HOST%:src/utils/
pscp -i %KEY% "%LOCAL_PATH%\src\utils\greeks_calculator.py" %HOST%:src/utils/
echo.

REM Transfer src/analyzers module
echo [*] Transferring analyzers module...
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\__init__.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\openbb_client.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\iv_analyzer.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\regime_analyzer.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\flow_analyzer.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\technical_analyzer.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\economic_calendar.py" %HOST%:src/analyzers/
pscp -i %KEY% "%LOCAL_PATH%\src\analyzers\sentiment_analyzer.py" %HOST%:src/analyzers/
echo.

REM Transfer src/strategies module
echo [*] Transferring strategies module...
pscp -i %KEY% "%LOCAL_PATH%\src\strategies\__init__.py" %HOST%:src/strategies/
pscp -i %KEY% "%LOCAL_PATH%\src\strategies\options_validator.py" %HOST%:src/strategies/
pscp -i %KEY% "%LOCAL_PATH%\src\strategies\multi_leg_manager.py" %HOST%:src/strategies/
pscp -i %KEY% "%LOCAL_PATH%\src\strategies\multi_leg_order_manager.py" %HOST%:src/strategies/
pscp -i %KEY% "%LOCAL_PATH%\src\strategies\multi_leg_tracker.py" %HOST%:src/strategies/
echo.

REM Transfer src/risk module
echo [*] Transferring risk module...
pscp -i %KEY% "%LOCAL_PATH%\src\risk\__init__.py" %HOST%:src/risk/
pscp -i %KEY% "%LOCAL_PATH%\src\risk\portfolio_manager.py" %HOST%:src/risk/
pscp -i %KEY% "%LOCAL_PATH%\src\risk\position_manager.py" %HOST%:src/risk/
echo.

REM Transfer src/scanners module
echo [*] Transferring scanners module...
pscp -i %KEY% "%LOCAL_PATH%\src\scanners\__init__.py" %HOST%:src/scanners/
pscp -i %KEY% "%LOCAL_PATH%\src\scanners\expert_scanner.py" %HOST%:src/scanners/
echo.

REM Transfer src/order_management module
echo [*] Transferring order_management module...
pscp -i %KEY% "%LOCAL_PATH%\src\order_management\__init__.py" %HOST%:src/order_management/
pscp -i %KEY% "%LOCAL_PATH%\src\order_management\batch_manager.py" %HOST%:src/order_management/
pscp -i %KEY% "%LOCAL_PATH%\src\order_management\replacement_analyzer.py" %HOST%:src/order_management/
echo.

REM Transfer src/connectors module
echo [*] Transferring connectors module...
pscp -i %KEY% "%LOCAL_PATH%\src\connectors\__init__.py" %HOST%:src/connectors/
pscp -i %KEY% "%LOCAL_PATH%\src\connectors\openbb_server.py" %HOST%:src/connectors/
echo.

REM Transfer src/bot_core
echo [*] Transferring bot_core module...
pscp -i %KEY% "%LOCAL_PATH%\src\bot_core.py" %HOST%:src/bot_core.py
echo.

REM Transfer src/test module
echo [*] Transferring test module...
pscp -i %KEY% "%LOCAL_PATH%\src\test\__init__.py" %HOST%:src/test/
pscp -i %KEY% "%LOCAL_PATH%\src\test\test_runner.py" %HOST%:src/test/
pscp -i %KEY% "%LOCAL_PATH%\src\test\test_config.py" %HOST%:src/test/
pscp -i %KEY% "%LOCAL_PATH%\src\test\test_risk.py" %HOST%:src/test/
pscp -i %KEY% "%LOCAL_PATH%\src\test\test_simple.py" %HOST%:src/test/
echo.

REM Transfer startup files
echo [*] Transferring startup files...
pscp -i %KEY% "%LOCAL_PATH%\start_bot.sh" %HOST%:start_bot.sh
echo.

