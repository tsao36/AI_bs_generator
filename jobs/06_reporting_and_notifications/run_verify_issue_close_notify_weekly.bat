@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
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
echo [INFO] Current dir: %CD%
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
