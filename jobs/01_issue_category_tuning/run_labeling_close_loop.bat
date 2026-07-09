@echo off
setlocal

cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: run_labeling_close_loop.bat ^<reviewed_labeling_file.csv^|.xlsx^> [threshold]
  echo Example: run_labeling_close_loop.bat tuning_outputs\weekly_20260412\weekly_labeling_template.xlsx 0.45
  exit /b 1
)

set "REVIEWED_CSV=%~1"
set "THRESHOLD=0.45"
if not "%~2"=="" set "THRESHOLD=%~2"

echo [STEP 1/3] Ingest reviewed labels
call .\ingest_weekly_reviewed_labels.bat "%REVIEWED_CSV%"
if errorlevel 1 exit /b 1

echo [STEP 2/3] Retrain model
call .\retrain_category_model.bat
if errorlevel 1 exit /b 1

echo [STEP 3/3] Re-run weekly validation/tuning report
call .\run_weekly_category_tuning.bat %THRESHOLD%
if errorlevel 1 exit /b 1

echo [OK] Close-loop complete.
echo [OK] Next: check newest folder under tuning_outputs\weekly_YYYYMMDD
echo [OK] Files to review: model_compare_metrics.json, best_model_confusion_matrix.csv, low_confidence_candidates.csv

exit /b 0
