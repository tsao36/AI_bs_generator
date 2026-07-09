@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "TUNING_ROOT=%PROJECT_ROOT%\tuning_outputs"
set "WEEKLY_OUTPUT_DIR=%PROJECT_ROOT%\CFE_input"
set "INPUT_DIRS=%PROJECT_ROOT%\CFE_reviewed_issue,%PROJECT_ROOT%\CFE_input"
set "ACTIVE_MODEL=%PROJECT_ROOT%\models\issue_category_model.joblib"
set "ACTIVE_METRICS=%PROJECT_ROOT%\models\issue_category_model_metrics.json"
set "CANDIDATE_DIR=%PROJECT_ROOT%\models\candidates"
set "ARCHIVE_DIR=%PROJECT_ROOT%\models\archive"

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
set "LOG_FILE=%LOG_DIR%\friday_auto_close_loop_%TS%.log"

REM Recommended scheduler command (no arguments):
REM   .\run_friday_auto_close_loop.bat
REM Recommended defaults:
REM   min_new_labels=10, min_gain=0.005, key_recall_drop_tolerance=0.02, threshold=0.45, allow_partial=1

set "MIN_NEW_LABELS=10"
set "MIN_GAIN=0.005"
set "KEY_DROP_TOL=0.02"
set "THRESHOLD=0.45"
set "ALLOW_PARTIAL=1"

if not "%~1"=="" set "MIN_NEW_LABELS=%~1"
if not "%~2"=="" set "MIN_GAIN=%~2"
if not "%~3"=="" set "KEY_DROP_TOL=%~3"
if not "%~4"=="" set "THRESHOLD=%~4"
if /i "%~5"=="--allow-partial" set "ALLOW_PARTIAL=1"

(
echo [INFO] ===== Friday auto close-loop start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Project root: %PROJECT_ROOT%
echo [INFO] tuning_root=%TUNING_ROOT%
echo [INFO] weekly_output_dir=%WEEKLY_OUTPUT_DIR%
echo [INFO] input_dirs=%INPUT_DIRS%
echo [INFO] min_new_labels=%MIN_NEW_LABELS% min_gain=%MIN_GAIN% key_recall_drop_tol=%KEY_DROP_TOL% threshold=%THRESHOLD% allow_partial=%ALLOW_PARTIAL%
echo [INFO] This batch does not send email notifications. It only writes result artifacts.
)>>"%LOG_FILE%"

echo [INFO] Friday recommended command: .\run_friday_auto_close_loop.bat
echo [INFO] Equivalent full args: .\run_friday_auto_close_loop.bat 10 0.005 0.02 0.45 --allow-partial
echo [INFO] Log file: %LOG_FILE%
echo [INFO] Python exe: %PY_EXE% %PY_ARGS%

echo [INFO] Friday auto close-loop starting...
echo [INFO] min_new_labels=%MIN_NEW_LABELS% min_gain=%MIN_GAIN% key_recall_drop_tol=%KEY_DROP_TOL% threshold=%THRESHOLD% allow_partial=%ALLOW_PARTIAL%

echo [STEP 1/2] Check weekly_labeling_template completion status
echo [STEP 1/2] Check weekly_labeling_template completion status>>"%LOG_FILE%"
where python >>"%LOG_FILE%" 2>&1
"%PY_EXE%" %PY_ARGS% check_weekly_labeling_status.py --tuning-root "%TUNING_ROOT%" >>"%LOG_FILE%" 2>&1
set "CHECK_EXIT=%ERRORLEVEL%"
echo [INFO] Step 1 exit code: !CHECK_EXIT!>>"%LOG_FILE%"
if not "!CHECK_EXIT!"=="0" (
  if "%ALLOW_PARTIAL%"=="1" (
    echo [WARN] Labeling precheck reported pending rows or check issue, but continuing due to --allow-partial.
    echo [WARN] Labeling precheck reported pending rows or check issue, but continuing due to --allow-partial.>>"%LOG_FILE%"
  ) else (
    echo [ERROR] Labeling precheck is not clean. Ask team to finish labeling, or rerun with --allow-partial.
    echo [ERROR] Labeling precheck is not clean. Ask team to finish labeling, or rerun with --allow-partial.>>"%LOG_FILE%"
    echo [INFO] Exit code: 2>>"%LOG_FILE%"
    echo [INFO] ===== Friday auto close-loop end =====>>"%LOG_FILE%"
    exit /b 2
  )
)

echo [STEP 2/2] Run Friday auto close-loop
echo [STEP 2/2] Run Friday auto close-loop>>"%LOG_FILE%"
"%PY_EXE%" %PY_ARGS% friday_auto_promote_model.py --tuning-root "%TUNING_ROOT%" --input-dirs "%INPUT_DIRS%" --weekly-output-dir "%WEEKLY_OUTPUT_DIR%" --active-model "%ACTIVE_MODEL%" --active-metrics "%ACTIVE_METRICS%" --candidate-dir "%CANDIDATE_DIR%" --archive-dir "%ARCHIVE_DIR%" --min-new-labels %MIN_NEW_LABELS% --min-gain %MIN_GAIN% --key-recall-drop-tolerance %KEY_DROP_TOL% --rerun-threshold %THRESHOLD% >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERROR] Friday auto close-loop failed.
  echo [ERROR] Friday auto close-loop failed.>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== Friday auto close-loop end =====>>"%LOG_FILE%"
  exit /b 1
)

echo [OK] Friday auto close-loop completed.
echo [OK] Check latest weekly folder for model_promotion_decision.json
echo [OK] Friday auto close-loop completed.>>"%LOG_FILE%"
echo [OK] Check latest weekly folder for model_promotion_decision.json>>"%LOG_FILE%"
echo [INFO] No email sent by design in this batch. If needed, add a post-step email script.>>"%LOG_FILE%"
echo [INFO] Exit code: 0>>"%LOG_FILE%"
echo [INFO] ===== Friday auto close-loop end =====>>"%LOG_FILE%"
exit /b 0
