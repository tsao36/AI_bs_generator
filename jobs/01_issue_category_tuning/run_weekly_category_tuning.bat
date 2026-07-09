@echo off
setlocal

cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\.."

set "THRESHOLD=0.45"
if not "%~1"=="" set "THRESHOLD=%~1"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "STAMP=%%I"
set "OUTPUT_DIR=%PROJECT_ROOT%\tuning_outputs\weekly_%STAMP%"
set "INPUT_DIRS=%PROJECT_ROOT%\CFE_reviewed_issue,%PROJECT_ROOT%\CFE_input"
set "CURRENT_MODEL=%PROJECT_ROOT%\models\issue_category_model.joblib"
set "PYTHON_EXE=%PROJECT_ROOT%\customer issue analysis\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"

echo [INFO] Running weekly category tuning...
echo [INFO] Output directory: %OUTPUT_DIR%
echo [INFO] Low-confidence threshold: %THRESHOLD%
echo [INFO] Input directories: %INPUT_DIRS%
echo [INFO] Current model: %CURRENT_MODEL%
echo [INFO] Python: %PYTHON_EXE%

"%PYTHON_EXE%" "%SCRIPT_DIR%run_category_tuning_cycle.py" --input-dirs "%INPUT_DIRS%" --current-model "%CURRENT_MODEL%" --output-dir "%OUTPUT_DIR%" --low-confidence-threshold %THRESHOLD%
if errorlevel 1 (
  echo [ERROR] Weekly tuning failed.
  exit /b 1
)

echo [OK] Weekly tuning complete.
echo [OK] Review these files:
echo   %OUTPUT_DIR%\model_compare_metrics.json
echo   %OUTPUT_DIR%\best_model_confusion_matrix.csv
echo   %OUTPUT_DIR%\low_confidence_candidates.csv

exit /b 0
