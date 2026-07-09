@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
  echo [ERROR] Failed to change directory to %SCRIPT_DIR%
  exit /b 1
)
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\management_monthly_customer_issue_report_%TS%.log"

set "YEAR=2026"
if not "%~1"=="" set "YEAR=%~1"
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\APIs;%PROJECT_ROOT%\jobs\01_issue_category_tuning;%PROJECT_ROOT%\jobs\03_offload_automation;%PROJECT_ROOT%\jobs\04_wireless_bug_dashboard;%PROJECT_ROOT%\jobs\06_reporting_and_notifications;%PYTHONPATH%"
if "%ISSUE_CATEGORY_PREDICT_BACKEND%"=="" set "ISSUE_CATEGORY_PREDICT_BACKEND=ml"
if "%GRAPH_AUTH_MODE%"=="" set "GRAPH_AUTH_MODE=app"

set "ENV_FILE=%PROJECT_ROOT%\.env"
if exist "%ENV_FILE%" (
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /c:"^[A-Za-z_][A-Za-z0-9_]*=" "%ENV_FILE%"`) do (
    if /I "%%A"=="AZURE_TENANT_ID" set "AZURE_TENANT_ID=%%B"
    if /I "%%A"=="AZURE_CLIENT_ID" set "AZURE_CLIENT_ID=%%B"
    if /I "%%A"=="GRAPH_CLIENT_SECRET" set "GRAPH_CLIENT_SECRET=%%B"
    if /I "%%A"=="GRAPH_SENDER_UPN" set "GRAPH_SENDER_UPN=%%B"
  )
)

set "PYTHON_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"
set "REPORT_SCRIPT=%PROJECT_ROOT%\customer issue analysis\send_management_monthly_issue_report.py"
set "RECIPIENTS_FILE=%PROJECT_ROOT%\customer issue analysis\recipients_management_monthly.json"
set "REPORT_MODEL=%PROJECT_ROOT%\models\issue_category_model.joblib"
set "REPORT_OUTPUT_DIR=%PROJECT_ROOT%\customer issue analysis\outputs"

(
echo [INFO] ===== Management monthly customer issue report start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Project root: %PROJECT_ROOT%
echo [INFO] Year: %YEAR%
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] PYTHONPATH: %PYTHONPATH%
echo [INFO] ISSUE_CATEGORY_PREDICT_BACKEND: %ISSUE_CATEGORY_PREDICT_BACKEND%
echo [INFO] GRAPH_AUTH_MODE: %GRAPH_AUTH_MODE%
echo [INFO] Report script: %REPORT_SCRIPT%
echo [INFO] Recipients: %RECIPIENTS_FILE%
echo [INFO] Model: %REPORT_MODEL%
echo [INFO] Output dir: %REPORT_OUTPUT_DIR%
if "%AZURE_TENANT_ID%"=="" echo [WARN] AZURE_TENANT_ID is empty.
if "%AZURE_CLIENT_ID%"=="" echo [WARN] AZURE_CLIENT_ID is empty.
if "%GRAPH_CLIENT_SECRET%"=="" echo [WARN] GRAPH_CLIENT_SECRET is empty. Batch will fail fast to avoid interactive prompt hang.
echo [INFO] Scope: all customers, %YEAR% only, simplified monthly management HTML
)>>"%LOG_FILE%"

echo [INFO] Running management monthly customer issue report for year %YEAR%
echo [INFO] Log file: %LOG_FILE%

if "%GRAPH_CLIENT_SECRET%"=="" (
  echo [ERROR] GRAPH_CLIENT_SECRET is missing. Set it in %ENV_FILE% or environment variables.
  echo [ERROR] GRAPH_CLIENT_SECRET is missing. Set it in %ENV_FILE% or environment variables.>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== Management monthly customer issue report end =====>>"%LOG_FILE%"
  exit /b 1
)

if "%AZURE_TENANT_ID%"=="" (
  echo [ERROR] AZURE_TENANT_ID is missing. Set it in %ENV_FILE% or environment variables.
  echo [ERROR] AZURE_TENANT_ID is missing. Set it in %ENV_FILE% or environment variables.>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== Management monthly customer issue report end =====>>"%LOG_FILE%"
  exit /b 1
)

if "%AZURE_CLIENT_ID%"=="" (
  echo [ERROR] AZURE_CLIENT_ID is missing. Set it in %ENV_FILE% or environment variables.
  echo [ERROR] AZURE_CLIENT_ID is missing. Set it in %ENV_FILE% or environment variables.>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== Management monthly customer issue report end =====>>"%LOG_FILE%"
  exit /b 1
)

"%PYTHON_EXE%" "%REPORT_SCRIPT%" --year %YEAR% --model "%REPORT_MODEL%" --output-dir "%REPORT_OUTPUT_DIR%" --recipients "%RECIPIENTS_FILE%" --allow-email-failure >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERROR] Management monthly report batch failed.
  echo [ERROR] Management monthly report batch failed.>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== Management monthly customer issue report end =====>>"%LOG_FILE%"
  exit /b 1
)

echo [OK] Management monthly customer issue report completed.
echo [OK] Management monthly customer issue report completed.>>"%LOG_FILE%"
echo [INFO] Exit code: 0>>"%LOG_FILE%"
echo [INFO] ===== Management monthly customer issue report end =====>>"%LOG_FILE%"
exit /b 0
