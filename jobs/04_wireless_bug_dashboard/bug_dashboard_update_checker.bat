@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "LOG_DIR=%SCRIPT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOGFILE=%LOG_DIR%\task_log_checker.txt"
set "PYTHON_EXE="
for %%P in (
  "C:\Users\jtsao1\AppData\Local\Python\pythoncore-3.14-64\python.exe"
  "C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe"
  "C:\Python314\python.exe"
) do (
  if not defined PYTHON_EXE if exist %%~P set "PYTHON_EXE=%%~P"
)
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
    )
)
if not defined PYTHON_EXE (
    echo [ERROR] Python executable not found on this server. >> "%LOGFILE%"
    exit /b 9001
)
set "NOTIFY_SCRIPT=%SCRIPT_DIR%\..\03_offload_automation\db_health_notify.py"
if not exist "%NOTIFY_SCRIPT%" set "NOTIFY_SCRIPT=%SCRIPT_DIR%\db_health_notify.py"
set VALIDATION_LOG=%SCRIPT_DIR%\db_vs_jira_validation.log
set DIFF_REPORT=%SCRIPT_DIR%\db_vs_jira_diff_report.txt

:: Move into the specific folder where your script lives
cd /d "%SCRIPT_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to change to directory: %SCRIPT_DIR% >> "%LOGFILE%"
    exit /b 1
)

:: Create a timestamp
set TIMESTAMP=%DATE% %TIME%

echo ============================================================ >> "%LOGFILE%"
echo [START] Task started at: %TIMESTAMP% >> "%LOGFILE%"
echo [INFO] Working Directory: %CD% >> "%LOGFILE%"
echo [INFO] Python Version: >> "%LOGFILE%"
"%PYTHON_EXE%" --version >> "%LOGFILE%" 2>&1

:: ---- Run the checker ----
echo [INFO] Executing: Bug_Dashboard_checker.py --log-file db_vs_jira_validation.log >> "%LOGFILE%"
"%PYTHON_EXE%" Bug_Dashboard_checker.py --log-file db_vs_jira_validation.log >> "%LOGFILE%" 2>&1

:: Capture exit code
set EXIT_CODE=%ERRORLEVEL%
set END_TIMESTAMP=%DATE% %TIME%

if %EXIT_CODE% EQU 0 (
    echo [SUCCESS] Script completed successfully with exit code: %EXIT_CODE% >> "%LOGFILE%"
    echo [SUCCESS] Validation check finished at: %END_TIMESTAMP% >> "%LOGFILE%"
) else (
    echo [ERROR] Script failed with exit code: %EXIT_CODE% >> "%LOGFILE%"
    echo [ERROR] Task failed at: %END_TIMESTAMP% >> "%LOGFILE%"
)

:: ---- Determine health status from output files ----
:: Priority 1: non-zero exit code = script crashed
:: Priority 2: db_vs_jira_diff_report.txt exists = differences found
:: Priority 3: db_vs_jira_validation.log contains ERROR line = error logged
set HEALTH_STATUS=ok
set HEALTH_DETAIL=

if %EXIT_CODE% NEQ 0 (
    set HEALTH_STATUS=error
    set HEALTH_DETAIL=script crashed, exit code %EXIT_CODE%
    goto :do_notify
)

if exist "%DIFF_REPORT%" (
    set HEALTH_STATUS=error
    set HEALTH_DETAIL=diff report exists - DB vs Jira mismatch detected
    goto :do_notify
)

findstr /i /c:"ERROR" "%VALIDATION_LOG%" >nul 2>&1
if not errorlevel 1 (
    set HEALTH_STATUS=error
    set HEALTH_DETAIL=ERROR found in db_vs_jira_validation.log
)

:do_notify
echo [INFO] Health status: !HEALTH_STATUS! (!HEALTH_DETAIL!) >> "%LOGFILE%"
if exist "%NOTIFY_SCRIPT%" (
    if "!HEALTH_STATUS!"=="ok" (
        "%PYTHON_EXE%" "%NOTIFY_SCRIPT%" --status ok >> "%LOGFILE%" 2>&1
    ) else (
        "%PYTHON_EXE%" "%NOTIFY_SCRIPT%" --status error --detail "!HEALTH_DETAIL!" >> "%LOGFILE%" 2>&1
    )
) else (
    echo [WARNING] db_health_notify.py not found. Skip health sentinel update. >> "%LOGFILE%"
)

echo [END] Task finished at: %END_TIMESTAMP% >> "%LOGFILE%"
echo ============================================================ >> "%LOGFILE%"
echo. >> "%LOGFILE%"

endlocal
exit /b %EXIT_CODE%