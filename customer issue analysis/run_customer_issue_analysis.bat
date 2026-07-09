@echo off
setlocal

cd /d "%~dp0\.."
set "PYTHON_EXE=customer issue analysis\.venv\Scripts\python.exe"

echo [INFO] Running customer issue analysis (2023-2026)...
"%PYTHON_EXE%" "customer issue analysis\customer_issue_analysis.py" --start-year 2023 --end-year 2026 --table ips_jira_bugs --model "models\issue_category_model.joblib" --output-dir "customer issue analysis\outputs"
if errorlevel 1 (
  echo [ERROR] Customer issue analysis failed.
  exit /b 1
)

echo [OK] Analysis complete. Outputs:
echo [OK] customer issue analysis\outputs\customer_issue_analysis_2023_2026.json
echo [OK] customer issue analysis\outputs\customer_issue_analysis_2023_2026_detail.csv
echo [OK] customer issue analysis\outputs\customer_issue_analysis_2023_2026_yoy.csv
exit /b 0
