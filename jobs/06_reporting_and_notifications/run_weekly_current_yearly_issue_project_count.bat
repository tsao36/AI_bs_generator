@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\APIs;%PROJECT_ROOT%\jobs\01_issue_category_tuning;%PROJECT_ROOT%\jobs\03_offload_automation;%PROJECT_ROOT%\jobs\04_wireless_bug_dashboard;%PROJECT_ROOT%\jobs\06_reporting_and_notifications;%PYTHONPATH%"
if "%DB_TABLE%"=="" set "DB_TABLE=ips_jira_bugs"

set "PYTHON_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"

set "ENV_FILE=%PROJECT_ROOT%\.env"
if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /c:"^[A-Za-z_][A-Za-z0-9_]*=" "%ENV_FILE%"`) do (
        if /I "%%A"=="AZURE_TENANT_ID" if "%AZURE_TENANT_ID%"=="" set "AZURE_TENANT_ID=%%B"
        if /I "%%A"=="AZURE_CLIENT_ID" if "%AZURE_CLIENT_ID%"=="" set "AZURE_CLIENT_ID=%%B"
        if /I "%%A"=="GRAPH_CLIENT_SECRET" if "%GRAPH_CLIENT_SECRET%"=="" set "GRAPH_CLIENT_SECRET=%%B"
        if /I "%%A"=="GRAPH_SENDER_UPN" if "%GRAPH_SENDER_UPN%"=="" set "GRAPH_SENDER_UPN=%%B"
        if /I "%%A"=="DEFAULT_TO" if "%GRAPH_SENDER_UPN%"=="" set "GRAPH_SENDER_UPN=%%B"
    )
)

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\weekly_current_yearly_issue_count_%TS%.log"

(
echo [INFO] ===== Weekly current yearly issue count start ======
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Current dir: %CD%
echo [INFO] Project root: %PROJECT_ROOT%
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] DB table: %DB_TABLE%
echo [INFO] PYTHONPATH: %PYTHONPATH%
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
        echo [ERROR] GRAPH_SENDER_UPN is not set. Configure it in .env or environment.>>"%LOG_FILE%"
        echo [ERROR] GRAPH_SENDER_UPN is not set.
        set "FINAL_EXIT_CODE=1"
        goto :finish
    )
)

where python >>"%LOG_FILE%" 2>&1
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python executable not found: %PYTHON_EXE%>>"%LOG_FILE%"
    echo [ERROR] Python executable not found: %PYTHON_EXE%
    set "FINAL_EXIT_CODE=1"
    goto :finish
)
echo [INFO] GRAPH_AUTH_MODE=%GRAPH_AUTH_MODE%>>"%LOG_FILE%"
echo [INFO] GRAPH_SENDER_UPN=%GRAPH_SENDER_UPN%>>"%LOG_FILE%"
echo [INFO] DB_TABLE=%DB_TABLE%>>"%LOG_FILE%"

echo [INFO] Weekly current yearly issue count start
echo [INFO] GRAPH_AUTH_MODE=%GRAPH_AUTH_MODE%
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] DB table: %DB_TABLE%
echo [INFO] Log file: %LOG_FILE%

:: ── Step 1: Issue contribution report ────────────────────────────────────────
echo [INFO] Running issue contribution report...>>"%LOG_FILE%"
"%PYTHON_EXE%" weekly_issue_count_report.py --table %DB_TABLE% --recipients recipients.json --graph-auth-mode %GRAPH_AUTH_MODE% %* >>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

(
echo [INFO] Issue report exit code: %EXIT_CODE%
)>>"%LOG_FILE%"

if "%EXIT_CODE%"=="0" (
    echo [OK] Issue contribution report sent successfully.
) else (
    echo [ERROR] Issue contribution report failed. See log: %LOG_FILE%
)

:: ── Step 2: DB-based project / customer issue loading report ────────────────
echo [INFO] Running DB project/customer loading report...>>"%LOG_FILE%"
"%PYTHON_EXE%" weekly_project_loading_from_db_report.py --table %DB_TABLE% --recipients recipients.json --graph-auth-mode %GRAPH_AUTH_MODE% %* >>"%LOG_FILE%" 2>&1
set "EXIT_CODE2=%ERRORLEVEL%"

(
echo [INFO] Project report exit code: %EXIT_CODE2%
echo [INFO] ===== Weekly current yearly issue count end =====
)>>"%LOG_FILE%"

if "%EXIT_CODE2%"=="0" (
    echo [OK] DB project/customer loading report sent successfully.
) else (
    echo [WARN] DB project/customer loading report failed. See log: %LOG_FILE%
)

:: Return non-zero only if issue report failed (project loading is supplemental)
if not "%EXIT_CODE%"=="0" (
    set "FINAL_EXIT_CODE=%EXIT_CODE%"
) else (
    set "FINAL_EXIT_CODE=0"
)

:finish
if "!FINAL_EXIT_CODE!"=="" set "FINAL_EXIT_CODE=0"
echo [INFO] Final exit code: !FINAL_EXIT_CODE!>>"%LOG_FILE%"
if not "!FINAL_EXIT_CODE!"=="0" (
    echo.
    echo [ERROR] Weekly current yearly issue/project count failed. Exit code: !FINAL_EXIT_CODE!
    echo [ERROR] See log: %LOG_FILE%
    if not "%WEEKLY_COUNT_NO_TIMEOUT%"=="1" timeout /t 60
)
exit /b !FINAL_EXIT_CODE!
