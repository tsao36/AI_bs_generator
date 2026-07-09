@echo off
setlocal

cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\APIs;%PROJECT_ROOT%\jobs\01_issue_category_tuning;%PROJECT_ROOT%\jobs\03_offload_automation;%PROJECT_ROOT%\jobs\04_wireless_bug_dashboard;%PROJECT_ROOT%\jobs\06_reporting_and_notifications;%PYTHONPATH%"
set "PYTHON_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"
set "TUNING_ROOT=%PROJECT_ROOT%\tuning_outputs"
set "MODEL_PATH=%PROJECT_ROOT%\models\issue_category_model.joblib"
set "RECIPIENTS_FILE=%PROJECT_ROOT%\jobs\06_reporting_and_notifications\recipients.json"

set "TOPN=all"
if not "%~1"=="" set "TOPN=%~1"

echo [INFO] Preparing weekly labeling template...
echo [INFO] Top N rows from last week's new issues: %TOPN% (use 'all' to include all)
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] Tuning root: %TUNING_ROOT%
echo [INFO] Model: %MODEL_PATH%
echo [INFO] Recipients: %RECIPIENTS_FILE%

"%PYTHON_EXE%" "%SCRIPT_DIR%prepare_weekly_labeling_template.py" --tuning-root "%TUNING_ROOT%" --model "%MODEL_PATH%" --recipients "%RECIPIENTS_FILE%" --top-n %TOPN%
if errorlevel 1 (
  echo [ERROR] Failed to prepare weekly labeling template.
  exit /b 1
)

echo [OK] Template generated from last week's new issues. Share weekly_labeling_template.csv with engineers.
exit /b 0
