@echo on
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "LOG_DIR=%SCRIPT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%W in ('powershell -NoProfile -Command "Get-Date -UFormat '%%Y-W%%V'"') do set "WEEK_TAG=%%W"
set "LOGFILE=%LOG_DIR%\task_log_dashboard_once_2022_%WEEK_TAG%.txt"
if exist "%SCRIPT_DIR%\task_log_dashboard_once_2022.txt" del /q "%SCRIPT_DIR%\task_log_dashboard_once_2022.txt"
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

set "PY_SCRIPT=wireless_bug_dashboard_ips_hsd_jira.py"
rem One-time backfill for year 2022 only.
rem NOTE: pipeline treats a single numeric year as rolling refresh; duplicate year forces exact-year mode.
set "PY_ARGS=--created-year 2022,2022 --hsd-limit 500 --hsd-owner-filter yaochien,timdaway --run-option 0 --db-append --no-menu"

if not exist "%PYTHON_EXE%" (
  >>"%LOGFILE%" echo [ERROR] Python executable not found: %PYTHON_EXE%
  exit /b 9001
)

if not exist "%SCRIPT_DIR%\%PY_SCRIPT%" (
  >>"%LOGFILE%" echo [ERROR] Script not found: %SCRIPT_DIR%\%PY_SCRIPT%
  exit /b 9002
)

cd /d "%SCRIPT_DIR%" || (>>"%LOGFILE%" echo [ERROR] Failed to change to directory: %SCRIPT_DIR% & exit /b 1)
set "PYTHONPATH=%SCRIPT_DIR%\APIs;%PYTHONPATH%"

set "START_TIMESTAMP=%DATE% %TIME%"
>>"%LOGFILE%" echo ============================================================
>>"%LOGFILE%" echo [START] One-time 2022 backfill started at: %START_TIMESTAMP%
>>"%LOGFILE%" echo [INFO] Working Directory: %CD%
>>"%LOGFILE%" echo [INFO] Python Version:
"%PYTHON_EXE%" --version >>"%LOGFILE%" 2>&1
>>"%LOGFILE%" echo [INFO] Executing: %PY_SCRIPT% %PY_ARGS%
>>"%LOGFILE%" echo [INFO] --- Python output begin ---

"%PYTHON_EXE%" "%PY_SCRIPT%" %PY_ARGS% >>"%LOGFILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
>>"%LOGFILE%" echo [INFO] --- Python output end ---

set "END_TIMESTAMP=%DATE% %TIME%"
if %EXIT_CODE% EQU 0 (
  >>"%LOGFILE%" echo [SUCCESS] One-time 2022 backfill completed successfully with exit code: %EXIT_CODE%
  >>"%LOGFILE%" echo [SUCCESS] Data update finished at: %END_TIMESTAMP%
) else (
  >>"%LOGFILE%" echo [ERROR] One-time 2022 backfill failed with exit code: %EXIT_CODE%
  >>"%LOGFILE%" echo [ERROR] Task failed at: %END_TIMESTAMP%
)

>>"%LOGFILE%" echo [END] Task finished at: %END_TIMESTAMP%
>>"%LOGFILE%" echo ============================================================
>>"%LOGFILE%" echo.

endlocal
exit /b %EXIT_CODE%
