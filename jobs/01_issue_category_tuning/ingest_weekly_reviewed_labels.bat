@echo off
setlocal

cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: ingest_weekly_reviewed_labels.bat ^<reviewed_file_path.csv^|.xlsx^>
  exit /b 1
)

set "REVIEWED=%~1"

echo [INFO] Ingesting reviewed labels from: %REVIEWED%
python ingest_reviewed_labels.py --reviewed-csv "%REVIEWED%" --output-dir CFE_input
if errorlevel 1 (
  echo [ERROR] Failed to ingest reviewed labels.
  exit /b 1
)

echo [OK] Reviewed labels are now in CFE_input and ready for retraining.
exit /b 0
