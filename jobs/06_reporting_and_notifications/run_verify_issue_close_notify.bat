@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)
if "%PROJECT_ROOT%"=="" set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\APIs;%PROJECT_ROOT%\jobs\01_issue_category_tuning;%PROJECT_ROOT%\jobs\03_offload_automation;%PROJECT_ROOT%\jobs\04_wireless_bug_dashboard;%PROJECT_ROOT%\jobs\06_reporting_and_notifications;%PYTHONPATH%"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"

if "%GRAPH_AUTH_MODE%"=="" set "GRAPH_AUTH_MODE=delegated"

echo [INFO] Running verify_issue_close_notify.py %*
echo [INFO] GRAPH_AUTH_MODE=%GRAPH_AUTH_MODE%
echo [INFO] GRAPH_SENDER_UPN=%GRAPH_SENDER_UPN%
echo [INFO] Python=%PYTHON_EXE%
"%PYTHON_EXE%" verify_issue_close_notify.py %*
set "EXIT_CODE=%ERRORLEVEL%"

echo [INFO] Finished with exit code %EXIT_CODE%
exit /b %EXIT_CODE%
