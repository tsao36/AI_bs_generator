@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "ENV_FILE=%PROJECT_ROOT%\.env"

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\weekly_kpi_health_check_%TS%.log"

set "MIN_ACCEPTED_LABELS=10"
set "GAIN_WINDOW=4"
set "TOP_CONFUSIONS=3"
set "PYTHONIOENCODING=utf-8"
set "ALLOW_EMAIL_FAILURE=1"
set "PYTHON_EXE=C:\Python314\python.exe"
set "TUNING_ROOT=%PROJECT_ROOT%\tuning_outputs"
set "MONDAY_LOG_DIR=%PROJECT_ROOT%\jobs\01_issue_category_tuning\logs"
set "SUPPLEMENT_SCRIPT=%PROJECT_ROOT%\jobs\01_issue_category_tuning\prepare_targeted_labeling_supplement.py"
set "KPI_SCRIPT=%SCRIPT_DIR%weekly_kpi_health_check.py"
set "KPI_EMAIL_SCRIPT=%SCRIPT_DIR%send_weekly_kpi_report_email.py"

if exist "%ENV_FILE%" (
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /r /c:"^[A-Za-z_][A-Za-z0-9_]*=" "%ENV_FILE%"`) do (
    if /I "%%A"=="AZURE_TENANT_ID" set "AZURE_TENANT_ID=%%B"
    if /I "%%A"=="AZURE_CLIENT_ID" set "AZURE_CLIENT_ID=%%B"
    if /I "%%A"=="GRAPH_CLIENT_SECRET" set "GRAPH_CLIENT_SECRET=%%B"
    if /I "%%A"=="GRAPH_SENDER_UPN" set "GRAPH_SENDER_UPN=%%B"
  )
)

if "%GRAPH_AUTH_MODE%"=="" set "GRAPH_AUTH_MODE=app"
if not "%KPI_ALLOW_EMAIL_FAILURE%"=="" set "ALLOW_EMAIL_FAILURE=%KPI_ALLOW_EMAIL_FAILURE%"

if not "%~1"=="" set "MIN_ACCEPTED_LABELS=%~1"
if not "%~2"=="" set "GAIN_WINDOW=%~2"
if not "%~3"=="" set "TOP_CONFUSIONS=%~3"

set "MONDAY_LOG="
set "MONDAY_COMPLETE=0"
set "PRECHECK_NOTE="
set "SUBJECT_PREFIX="

for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "$f=Get-ChildItem '%MONDAY_LOG_DIR%' -Filter 'weekly_tuning_prepare_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName; if($f){$f}"`) do set "MONDAY_LOG=%%L"

if not defined MONDAY_LOG (
  set "PRECHECK_NOTE=ACTION REQUIRED: Monday pipeline completion log not found. Please run run_weekly_tuning_and_prepare_labeling.bat."
  set "SUBJECT_PREFIX=[ACTION REQUIRED]"
  echo [WARN] !PRECHECK_NOTE!>>"%LOG_FILE%"
) else (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -Command "$p='%MONDAY_LOG%'; if(-not (Test-Path $p)){ '0'; exit }; $isRecent=((Get-Date)-(Get-Item $p).LastWriteTime).TotalDays -le 7; $ok1=Select-String -Path $p -Pattern '\[OK\] Weekly tuning \+ labeling preparation \+ reminder complete\.' -Quiet; $ok2=Select-String -Path $p -Pattern '\[OK\] Targeted supplement rows:\s*\d+' -Quiet; if($isRecent -and $ok1 -and $ok2){'1'} else {'0'}"`) do set "MONDAY_COMPLETE=%%R"
  if not "!MONDAY_COMPLETE!"=="1" (
    set "PRECHECK_NOTE=ACTION REQUIRED: Monday pipeline appears incomplete or stale. Check !MONDAY_LOG!"
    set "SUBJECT_PREFIX=[ACTION REQUIRED]"
    echo [WARN] !PRECHECK_NOTE!>>"%LOG_FILE%"
  ) else (
    echo [INFO] Monday pipeline completion check passed: !MONDAY_LOG!>>"%LOG_FILE%"
  )
)

(
echo [INFO] ===== Weekly KPI health check start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Project root: %PROJECT_ROOT%
echo [INFO] Tuning root: %TUNING_ROOT%
echo [INFO] GRAPH_AUTH_MODE=%GRAPH_AUTH_MODE%
if "%AZURE_TENANT_ID%"=="" echo [WARN] AZURE_TENANT_ID is empty.
if "%AZURE_CLIENT_ID%"=="" echo [WARN] AZURE_CLIENT_ID is empty.
if "%GRAPH_CLIENT_SECRET%"=="" echo [WARN] GRAPH_CLIENT_SECRET is empty.
echo [INFO] min_accepted_labels=%MIN_ACCEPTED_LABELS% gain_window=%GAIN_WINDOW% top_confusions=%TOP_CONFUSIONS%
)>>"%LOG_FILE%"

echo [INFO] Weekly KPI health check start
echo [INFO] min_accepted_labels=%MIN_ACCEPTED_LABELS% gain_window=%GAIN_WINDOW% top_confusions=%TOP_CONFUSIONS%
echo [INFO] Log file: %LOG_FILE%

"%PYTHON_EXE%" "%KPI_SCRIPT%" --tuning-root "%TUNING_ROOT%" --min-accepted-labels %MIN_ACCEPTED_LABELS% --gain-window %GAIN_WINDOW% --top-confusions %TOP_CONFUSIONS% >>"%LOG_FILE%" 2>&1
set "KPI_EXIT=%ERRORLEVEL%"
echo [INFO] Generating targeted labeling supplement...>>"%LOG_FILE%"
echo [INFO] Generating targeted labeling supplement...
set "SUPPLEMENT_FILE=targeted_labeling_supplement_%TS:~0,8%.csv"
"%PYTHON_EXE%" "%SUPPLEMENT_SCRIPT%" --out "%SUPPLEMENT_FILE%" >>"%LOG_FILE%" 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo [WARN] Supplement generation failed, continuing without it.>>"%LOG_FILE%"
  echo [WARN] Supplement generation failed, continuing without it.
  set "SUPPLEMENT_FILE="
)
echo [INFO] Sending KPI email report to tsao36@gmail.com>>"%LOG_FILE%"
echo [INFO] Sending KPI email report to tsao36@gmail.com
set "EMAIL_EXIT=0"
if "%AZURE_TENANT_ID%"=="" set "EMAIL_EXIT=9001"
if "%AZURE_CLIENT_ID%"=="" set "EMAIL_EXIT=9001"
if "%GRAPH_CLIENT_SECRET%"=="" set "EMAIL_EXIT=9001"

if "%EMAIL_EXIT%"=="0" (
  "%PYTHON_EXE%" "%KPI_EMAIL_SCRIPT%" --graph-auth-mode "%GRAPH_AUTH_MODE%" --tuning-root "%TUNING_ROOT%" --to tsao36@gmail.com --supplement "%SUPPLEMENT_FILE%" --subject-prefix "%SUBJECT_PREFIX%" --extra-note "%PRECHECK_NOTE%" >>"%LOG_FILE%" 2>&1
  set "EMAIL_EXIT=%ERRORLEVEL%"
) else (
  echo [WARN] Missing Graph env settings, skip KPI email sending.>>"%LOG_FILE%"
  echo [WARN] Missing Graph env settings, skip KPI email sending.
)

if not "!EMAIL_EXIT!"=="0" (
  if "!ALLOW_EMAIL_FAILURE!"=="1" (
    echo [WARN] Failed to send KPI email, but continuing due to KPI_ALLOW_EMAIL_FAILURE=1.>>"%LOG_FILE%"
    echo [WARN] Failed to send KPI email, but continuing due to KPI_ALLOW_EMAIL_FAILURE=1.
    set "EMAIL_EXIT=0"
  ) else (
    echo [ERROR] Failed to send KPI email.>>"%LOG_FILE%"
    echo [ERROR] Failed to send KPI email.
    echo [INFO] Exit code: !EMAIL_EXIT!>>"%LOG_FILE%"
    echo [INFO] ===== Weekly KPI health check end =====>>"%LOG_FILE%"
    exit /b !EMAIL_EXIT!
  )
)

set "EXIT_CODE=%KPI_EXIT%"

if "!EXIT_CODE!"=="0" (
  echo [OK] KPI checks passed.>>"%LOG_FILE%"
  echo [OK] KPI checks passed.
  echo [INFO] Exit code: 0>>"%LOG_FILE%"
  echo [INFO] ===== Weekly KPI health check end =====>>"%LOG_FILE%"
  exit /b 0
)

if "!EXIT_CODE!"=="2" (
  echo [WARN] KPI checks did not fully pass. Review weekly_kpi_health_check.json in latest weekly folder.>>"%LOG_FILE%"
  echo [WARN] KPI checks did not fully pass. Review weekly_kpi_health_check.json in latest weekly folder.
  echo [INFO] Exit code: 2>>"%LOG_FILE%"
  echo [INFO] ===== Weekly KPI health check end =====>>"%LOG_FILE%"
  exit /b 2
)

echo [ERROR] KPI health check failed unexpectedly.>>"%LOG_FILE%"
echo [ERROR] KPI health check failed unexpectedly.
echo [INFO] Exit code: !EXIT_CODE!>>"%LOG_FILE%"
echo [INFO] ===== Weekly KPI health check end =====>>"%LOG_FILE%"
exit /b !EXIT_CODE!
