@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\APIs;%PROJECT_ROOT%\jobs\01_issue_category_tuning;%PROJECT_ROOT%\jobs\03_offload_automation;%PROJECT_ROOT%\jobs\04_wireless_bug_dashboard;%PROJECT_ROOT%\jobs\06_reporting_and_notifications;%PYTHONPATH%"
set "PYTHON_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"

set "ENV_FILE=%PROJECT_ROOT%\.env"
if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /c:"^[A-Za-z_][A-Za-z0-9_]*=" "%ENV_FILE%"`) do (
        if /I "%%A"=="AZURE_TENANT_ID" if "!AZURE_TENANT_ID!"=="" set "AZURE_TENANT_ID=%%B"
        if /I "%%A"=="AZURE_CLIENT_ID" if "!AZURE_CLIENT_ID!"=="" set "AZURE_CLIENT_ID=%%B"
        if /I "%%A"=="GRAPH_CLIENT_SECRET" if "!GRAPH_CLIENT_SECRET!"=="" set "GRAPH_CLIENT_SECRET=%%B"
        if /I "%%A"=="GRAPH_SENDER_UPN" if "!GRAPH_SENDER_UPN!"=="" set "GRAPH_SENDER_UPN=%%B"
    )
)

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\verify_issue_close_notify_weekly_%TS%.log"

(
echo [INFO] ===== Verify-issue close reminder weekly start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Project root: %PROJECT_ROOT%
echo [INFO] Current dir: %CD%
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] PYTHONPATH: %PYTHONPATH%
echo [INFO] ENV file: %ENV_FILE%
)>>"%LOG_FILE%"

if "%GRAPH_AUTH_MODE%"=="" set "GRAPH_AUTH_MODE=delegated"

if /i "%GRAPH_AUTH_MODE%"=="app" (
    if "%GRAPH_SENDER_UPN%"=="" (
        if exist "%SCRIPT_DIR%.env" (
            for /f "usebackq tokens=1,* delims==" %%A in ("%SCRIPT_DIR%.env") do (
                if /i "%%~A"=="GRAPH_SENDER_UPN" set "GRAPH_SENDER_UPN=%%~B"
                if /i "%%~A"=="DEFAULT_TO" if "%GRAPH_SENDER_UPN%"=="" set "GRAPH_SENDER_UPN=%%~B"
            )
        )
    )
    for /f "tokens=1 delims=,; " %%i in ("%GRAPH_SENDER_UPN%") do set "GRAPH_SENDER_UPN=%%~i"

    if "%GRAPH_SENDER_UPN%"=="" (
        echo [ERROR] GRAPH_SENDER_UPN is not set. Configure it in .env or Task Scheduler environment.>>"%LOG_FILE%"
        exit /b 1
    )
)

where python >>"%LOG_FILE%" 2>&1
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python executable not found: %PYTHON_EXE%>>"%LOG_FILE%"
    exit /b 1
)

if "%AZURE_TENANT_ID%"=="" (
    echo [ERROR] AZURE_TENANT_ID is missing. Set it in %ENV_FILE% or Task Scheduler environment.>>"%LOG_FILE%"
    exit /b 1
)
if "%AZURE_TENANT_ID%"=="https://login.microsoftonline.com/" (
    echo [ERROR] AZURE_TENANT_ID is invalid: https://login.microsoftonline.com/ . Use tenant GUID or tenant domain only.>>"%LOG_FILE%"
    exit /b 1
)
if "%AZURE_CLIENT_ID%"=="" (
    echo [ERROR] AZURE_CLIENT_ID is missing. Set it in %ENV_FILE% or Task Scheduler environment.>>"%LOG_FILE%"
    exit /b 1
)
if "%GRAPH_CLIENT_SECRET%"=="" (
    echo [ERROR] GRAPH_CLIENT_SECRET is missing. Set it in %ENV_FILE% or Task Scheduler environment.>>"%LOG_FILE%"
    exit /b 1
)

echo [INFO] GRAPH_AUTH_MODE=%GRAPH_AUTH_MODE%>>"%LOG_FILE%"
echo [INFO] GRAPH_SENDER_UPN=%GRAPH_SENDER_UPN%>>"%LOG_FILE%"

echo [INFO] Command: run_verify_issue_close_notify.bat --send-email %*>>"%LOG_FILE%"
call "%SCRIPT_DIR%run_verify_issue_close_notify.bat" --send-email %* >>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

(
echo [INFO] Exit code: %EXIT_CODE%
echo [INFO] ===== Verify-issue close reminder weekly end =====
)>>"%LOG_FILE%"

exit /b %EXIT_CODE%
