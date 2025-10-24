@echo off

echo ===========================================
echo Deploying DUBK Options Trading Bot to EC2
echo Using: git pull on remote host
echo ===========================================
echo.

REM Configuration
set KEY="C:\Users\kyler\Downloads\Trading.ppk"
set HOST=ubuntu@ec2-3-101-103-242.us-west-1.compute.amazonaws.com
set REPO_URL=git@github.com:Remdon/DubK_Options.git
set APP_DIR=/home/ubuntu/DubK_Options

echo [*] Ensuring Git is installed on EC2 and pulling latest code...
plink -i %KEY% %HOST% "bash -lc 'set -e; \
  if ! command -v git >/dev/null 2>&1; then \
    echo Installing git...; sudo apt-get update -y && sudo apt-get install -y git; \
  fi; \
  if [ -d \"%APP_DIR%/.git\" ]; then \
    echo Updating existing repo at %APP_DIR% ...; \
    cd \"%APP_DIR%\" && git fetch --all && git reset --hard origin/main && git submodule update --init --recursive; \
  else \
    echo Cloning repo to %APP_DIR% ...; \
    git clone \"%REPO_URL%\" \"%APP_DIR%\"; \
  fi'"

IF %ERRORLEVEL% NEQ 0 (
  echo Deployment failed.
  exit /b 1
)

echo [*] Done. Remote now matches origin/main.

REM Optional: install/update Python deps on EC2
REM plink -i %KEY% %HOST% "bash -lc 'cd %APP_DIR% && python3 -m venv .venv && . .venv/bin/activate && pip install -U pip && pip install -r requirements_openbb.txt'"

