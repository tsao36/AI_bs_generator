@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\friday_model_update_and_summary_%TS%.log"

set "FRI_MIN_NEW=%~1"
set "FRI_MIN_GAIN=%~2"
set "FRI_KEY_DROP=%~3"
set "FRI_THRESHOLD=%~4"
set "FRI_ALLOW_PARTIAL=%~5"

set "KPI_MIN_ACCEPTED=%~6"
set "KPI_GAIN_WINDOW=%~7"
set "KPI_TOP_CONF=%~8"
set "KPI_BATCH=%PROJECT_ROOT%\jobs\06_reporting_and_notifications\run_weekly_kpi_health_check.bat"

(
echo [INFO] ===== Friday model update + summary start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Friday args: %FRI_MIN_NEW% %FRI_MIN_GAIN% %FRI_KEY_DROP% %FRI_THRESHOLD% %FRI_ALLOW_PARTIAL%
echo [INFO] KPI args: %KPI_MIN_ACCEPTED% %KPI_GAIN_WINDOW% %KPI_TOP_CONF%
echo [INFO] KPI batch: %KPI_BATCH%
)>>"%LOG_FILE%"

if not exist "%KPI_BATCH%" (
  echo [ERROR] KPI batch not found: %KPI_BATCH%
  echo [ERROR] KPI batch not found: %KPI_BATCH%>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== Friday model update + summary end =====>>"%LOG_FILE%"
  exit /b 1
)

echo [STEP 1/2] Friday auto close-loop
echo [STEP 1/2] Friday auto close-loop>>"%LOG_FILE%"
call "%SCRIPT_DIR%run_friday_auto_close_loop.bat" %FRI_MIN_NEW% %FRI_MIN_GAIN% %FRI_KEY_DROP% %FRI_THRESHOLD% %FRI_ALLOW_PARTIAL% >>"%LOG_FILE%" 2>&1
set "FRIDAY_EXIT=!ERRORLEVEL!"
echo [INFO] Friday close-loop exit: !FRIDAY_EXIT!>>"%LOG_FILE%"
if not "!FRIDAY_EXIT!"=="0" (
  echo [ERROR] Friday auto close-loop failed with exit !FRIDAY_EXIT!.
  echo [ERROR] Friday auto close-loop failed with exit !FRIDAY_EXIT!.>>"%LOG_FILE%"
  echo [INFO] ===== Friday model update + summary end =====>>"%LOG_FILE%"
  exit /b !FRIDAY_EXIT!
)

echo [STEP 2/2] Weekly KPI summary email
echo [STEP 2/2] Weekly KPI summary email>>"%LOG_FILE%"
call "%KPI_BATCH%" %KPI_MIN_ACCEPTED% %KPI_GAIN_WINDOW% %KPI_TOP_CONF% >>"%LOG_FILE%" 2>&1
set "KPI_EXIT=!ERRORLEVEL!"
echo [INFO] KPI batch exit: !KPI_EXIT!>>"%LOG_FILE%"

if "!KPI_EXIT!"=="0" (
  echo [OK] Friday model update + summary email completed.
  echo [OK] Friday model update + summary email completed.>>"%LOG_FILE%"
  echo [INFO] Exit code: 0>>"%LOG_FILE%"
  echo [INFO] ===== Friday model update + summary end =====>>"%LOG_FILE%"
  exit /b 0
)

if "!KPI_EXIT!"=="2" (
  echo [WARN] Summary email sent; KPI checks not fully passed. exit=2
  echo [WARN] Summary email sent; KPI checks not fully passed. exit=2>>"%LOG_FILE%"
  echo [INFO] Exit code: 2>>"%LOG_FILE%"
  echo [INFO] ===== Friday model update + summary end =====>>"%LOG_FILE%"
  exit /b 2
)

echo [ERROR] Weekly KPI summary email batch failed with exit !KPI_EXIT!.
echo [ERROR] Weekly KPI summary email batch failed with exit !KPI_EXIT!.>>"%LOG_FILE%"
echo [INFO] Exit code: !KPI_EXIT!>>"%LOG_FILE%"
echo [INFO] ===== Friday model update + summary end =====>>"%LOG_FILE%"
exit /b !KPI_EXIT!
