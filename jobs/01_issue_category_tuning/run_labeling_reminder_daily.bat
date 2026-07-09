@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "TUNING_ROOT=%PROJECT_ROOT%\tuning_outputs"
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\APIs;%PROJECT_ROOT%\jobs\06_reporting_and_notifications;%PYTHONPATH%"
if "%GRAPH_AUTH_MODE%"=="" set "GRAPH_AUTH_MODE=app"
if "%LABELING_REMINDER_ALLOW_EMAIL_FAILURE%"=="" set "LABELING_REMINDER_ALLOW_EMAIL_FAILURE=1"
if exist "%PROJECT_ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /c:"^[A-Za-z_][A-Za-z0-9_]*=" "%PROJECT_ROOT%\.env"`) do (
        if /i "%%~A"=="GRAPH_CLIENT_SECRET" if "%GRAPH_CLIENT_SECRET%"=="" set "GRAPH_CLIENT_SECRET=%%~B"
        if /i "%%~A"=="AZURE_TENANT_ID" if "%AZURE_TENANT_ID%"=="" set "AZURE_TENANT_ID=%%~B"
        if /i "%%~A"=="AZURE_CLIENT_ID" if "%AZURE_CLIENT_ID%"=="" set "AZURE_CLIENT_ID=%%~B"
        if /i "%%~A"=="GRAPH_SENDER_UPN" if "%GRAPH_SENDER_UPN%"=="" set "GRAPH_SENDER_UPN=%%~B"
        if /i "%%~A"=="DEFAULT_TO" if "%GRAPH_SENDER_UPN%"=="" set "GRAPH_SENDER_UPN=%%~B"
    )
)

set "PY_EXE=python"
set "PY_ARGS="
if exist "%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe" (
    set "PY_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
) else (
    py -3.14 --version >nul 2>nul
    if not errorlevel 1 (
        set "PY_EXE=py"
        set "PY_ARGS=-3.14"
    )
)

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\labeling_reminder_daily_%TS%.log"

(
echo [INFO] ===== Labeling reminder daily start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Current dir: %CD%
echo [INFO] Project root: %PROJECT_ROOT%
echo [INFO] tuning_root: %TUNING_ROOT%
echo [INFO] GRAPH_AUTH_MODE: %GRAPH_AUTH_MODE%
echo [INFO] GRAPH_SENDER_UPN: %GRAPH_SENDER_UPN%
echo [INFO] LABELING_REMINDER_ALLOW_EMAIL_FAILURE: %LABELING_REMINDER_ALLOW_EMAIL_FAILURE%
echo [INFO] PYTHONPATH: %PYTHONPATH%
echo [INFO] PY_EXE: %PY_EXE%
echo [INFO] PY_ARGS: %PY_ARGS%
)>>"%LOG_FILE%"

if "%GRAPH_SENDER_UPN%"=="" (
    echo [ERROR] GRAPH_SENDER_UPN is empty. Set it in environment or .env.>>"%LOG_FILE%"
    set "EXIT_CODE=1"
    goto :done
)
if "%GRAPH_CLIENT_SECRET%"=="" (
    echo [ERROR] GRAPH_CLIENT_SECRET is empty. Set it in environment or .env to avoid interactive prompt in Task Scheduler.>>"%LOG_FILE%"
    set "EXIT_CODE=1"
    goto :done
)

where python >>"%LOG_FILE%" 2>&1

set "LATEST_WEEKLY_DIR="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$base='%TUNING_ROOT%'; $dirs=Get-ChildItem -Path $base -Directory -Filter 'weekly_*' -ErrorAction SilentlyContinue; $dated=$dirs | Where-Object { $_.Name -match '^weekly_(\d{8})$' } | Sort-Object { [int]$_.Name.Substring(7,8) } -Descending; if($dated){$d=$dated[0].FullName}else{$d=($dirs | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName)}; if($d){$d}"`) do set "LATEST_WEEKLY_DIR=%%i"

if "%LATEST_WEEKLY_DIR%"=="" (
    echo [ERROR] No weekly_* directory found under %TUNING_ROOT%.>>"%LOG_FILE%"
    set "EXIT_CODE=1"
    goto :done
)

set "TARGET_CSV=%LATEST_WEEKLY_DIR%\weekly_labeling_template.csv"
if not exist "%TARGET_CSV%" (
    echo [ERROR] Expected CSV not found: %TARGET_CSV%>>"%LOG_FILE%"
    set "EXIT_CODE=1"
    goto :done
)

echo [INFO] Latest weekly dir: %LATEST_WEEKLY_DIR%>>"%LOG_FILE%"
echo [INFO] Target CSV: %TARGET_CSV%>>"%LOG_FILE%"
set "ALLOW_EMAIL_FAILURE_ARG="
if "%LABELING_REMINDER_ALLOW_EMAIL_FAILURE%"=="1" set "ALLOW_EMAIL_FAILURE_ARG=--allow-email-failure"

echo [INFO] Command: "%PY_EXE%" %PY_ARGS% send_labeling_reminders.py --send-email --graph-auth-mode %GRAPH_AUTH_MODE% --weekly-dir "%LATEST_WEEKLY_DIR%" %ALLOW_EMAIL_FAILURE_ARG% %*>>"%LOG_FILE%"
"%PY_EXE%" %PY_ARGS% send_labeling_reminders.py --send-email --graph-auth-mode %GRAPH_AUTH_MODE% --weekly-dir "%LATEST_WEEKLY_DIR%" %ALLOW_EMAIL_FAILURE_ARG% %* >>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

:done
(
echo [INFO] Exit code: %EXIT_CODE%
echo [INFO] ===== Labeling reminder daily end =====
)>>"%LOG_FILE%"

exit /b %EXIT_CODE%
