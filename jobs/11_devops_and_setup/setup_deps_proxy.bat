@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)

set "PROXY_URL=http://proxy-dmz.intel.com:912"
if not "%~1"=="" set "PROXY_URL=%~1"

echo [INFO] Using proxy: %PROXY_URL%
echo [INFO] Installing required Python packages...

python -m pip install --proxy %PROXY_URL% snowflake-connector-python || goto :fail
python -m pip install --proxy %PROXY_URL% "psycopg[binary]" || goto :fail
python -m pip install --proxy %PROXY_URL% jira || goto :fail

echo [INFO] Dependency installation completed successfully.
exit /b 0

:fail
echo [ERROR] Dependency installation failed.
exit /b 1
