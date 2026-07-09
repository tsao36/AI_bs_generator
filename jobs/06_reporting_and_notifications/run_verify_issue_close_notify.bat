@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)

if "%GRAPH_AUTH_MODE%"=="" set "GRAPH_AUTH_MODE=delegated"

echo [INFO] Running verify_issue_close_notify.py %*
echo [INFO] GRAPH_AUTH_MODE=%GRAPH_AUTH_MODE%
echo [INFO] GRAPH_SENDER_UPN=%GRAPH_SENDER_UPN%
python verify_issue_close_notify.py %*
set "EXIT_CODE=%ERRORLEVEL%"

echo [INFO] Finished with exit code %EXIT_CODE%
exit /b %EXIT_CODE%
