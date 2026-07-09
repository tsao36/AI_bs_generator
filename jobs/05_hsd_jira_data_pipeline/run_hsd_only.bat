@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
	echo [ERROR] Failed to change directory to %SCRIPT_DIR%
	exit /b 1
)

set "ROOT_DIR=%SCRIPT_DIR%..\.."
set "PIPELINE_SCRIPT=%ROOT_DIR%\jobs\04_wireless_bug_dashboard\wireless_bug_dashboard_ips_hsd_jira.py"
set "PYTHONPATH=%ROOT_DIR%;%ROOT_DIR%\APIs;%ROOT_DIR%\jobs\04_wireless_bug_dashboard;%ROOT_DIR%\jobs\05_hsd_jira_data_pipeline;%PYTHONPATH%"

set "PYTHON_EXE=C:\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if "%HSD_QUERY_ID%"=="" set "HSD_QUERY_ID=15019575844"
if "%HSD_CREATED_YEARS%"=="" set "HSD_CREATED_YEARS=2021,2022,2023,2024,2025,2026,2027"
if "%HSD_LIMIT%"=="" set "HSD_LIMIT=5000"
if "%HSD_OWNER_FILTER%"=="" set "HSD_OWNER_FILTER=szchen,fang,kj,kjfang,timdaway,frankfcy,yuweich1,brentonw,chenmatt,caizhiqi,flee5,wesleyku,bingyues,jzou6,chuchar1"

set LOG_DIR=%SCRIPT_DIR%logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
set "LOG_FILE=%LOG_DIR%\hsd_only_%TS%.log"

echo [%DATE% %TIME%] Starting HSD-only job > "%LOG_FILE%"
echo [INFO] Python executable: !PYTHON_EXE! >> "%LOG_FILE%"
echo [INFO] ROOT_DIR=%ROOT_DIR% >> "%LOG_FILE%"
echo [INFO] PIPELINE_SCRIPT=%PIPELINE_SCRIPT% >> "%LOG_FILE%"
echo [INFO] PYTHONPATH=%PYTHONPATH% >> "%LOG_FILE%"
echo [INFO] HSD_QUERY_ID=%HSD_QUERY_ID% >> "%LOG_FILE%"
echo [INFO] HSD_CREATED_YEARS=%HSD_CREATED_YEARS% >> "%LOG_FILE%"
echo [INFO] HSD_LIMIT=%HSD_LIMIT% >> "%LOG_FILE%"
echo [INFO] HSD_OWNER_FILTER=%HSD_OWNER_FILTER% >> "%LOG_FILE%"
"!PYTHON_EXE!" --version >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
	echo [WARN] Selected Python is not runnable. Falling back to PATH python. >> "%LOG_FILE%"
	set "PYTHON_EXE=python"
	echo [INFO] Python executable fallback: !PYTHON_EXE! >> "%LOG_FILE%"
	"!PYTHON_EXE!" --version >> "%LOG_FILE%" 2>&1
)

"!PYTHON_EXE!" -c "import psycopg2" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
	echo [WARN] Selected Python lacks psycopg2. Trying C:\Python314\python.exe. >> "%LOG_FILE%"
	if exist "C:\Python314\python.exe" set "PYTHON_EXE=C:\Python314\python.exe"
	"!PYTHON_EXE!" -c "import psycopg2" >> "%LOG_FILE%" 2>&1
)
if errorlevel 1 (
	echo [WARN] C:\Python314\python.exe lacks psycopg2 or is unavailable. Trying PATH python. >> "%LOG_FILE%"
	set "PYTHON_EXE=python"
	"!PYTHON_EXE!" -c "import psycopg2" >> "%LOG_FILE%" 2>&1
)
if errorlevel 1 (
	echo [ERROR] No runnable Python with psycopg2 was found. Install psycopg2 or fix PYTHON_EXE. >> "%LOG_FILE%"
	exit /b 1
)
echo [INFO] Python with psycopg2: !PYTHON_EXE! >> "%LOG_FILE%"

rem Resolve certifi CA bundle from selected Python
set "REQUESTS_CA_BUNDLE="
for /f "delims=" %%i in ('"!PYTHON_EXE!" -m certifi 2^>nul') do set "REQUESTS_CA_BUNDLE=%%i"
if not defined REQUESTS_CA_BUNDLE set "REQUESTS_CA_BUNDLE=C:\Program Files\Python314\Lib\site-packages\certifi\cacert.pem"
echo [INFO] REQUESTS_CA_BUNDLE=%REQUESTS_CA_BUNDLE% >> "%LOG_FILE%"

"!PYTHON_EXE!" "%PIPELINE_SCRIPT%" --hsd-only --hsd-insecure --created-year %HSD_CREATED_YEARS% --hsd-query-id %HSD_QUERY_ID% --hsd-limit %HSD_LIMIT% --hsd-table ips_jira_bugs --hsd-owner-filter "%HSD_OWNER_FILTER%" --db-batch --log-level INFO >> "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%
echo [%DATE% %TIME%] Finished with exit code %EXIT_CODE% >> "%LOG_FILE%"

exit /b %EXIT_CODE%
