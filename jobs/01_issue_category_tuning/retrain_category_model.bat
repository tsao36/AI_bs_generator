@echo off
setlocal

cd /d "%~dp0"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%I"
set "ARCHIVE_DIR=models\archive"
if not exist "%ARCHIVE_DIR%" mkdir "%ARCHIVE_DIR%"

if exist "models\issue_category_model.joblib" copy /Y "models\issue_category_model.joblib" "%ARCHIVE_DIR%\issue_category_model_%STAMP%.joblib" >nul
if exist "models\issue_category_model_metrics.json" copy /Y "models\issue_category_model_metrics.json" "%ARCHIVE_DIR%\issue_category_model_metrics_%STAMP%.json" >nul

echo [INFO] Retraining category model from CFE_reviewed_issue + CFE_input...
python train_issue_category_model.py --input-dirs CFE_reviewed_issue,CFE_input --model-out models\issue_category_model.joblib --metrics-out models\issue_category_model_metrics.json
if errorlevel 1 (
  echo [ERROR] Retrain failed. Previous model backup kept in %ARCHIVE_DIR%.
  exit /b 1
)

echo [OK] Retrain complete.
echo [OK] New model: models\issue_category_model.joblib
echo [OK] Metrics: models\issue_category_model_metrics.json
exit /b 0
