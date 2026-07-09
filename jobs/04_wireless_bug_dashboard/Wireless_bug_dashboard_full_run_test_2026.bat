
@echo on
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "LOG_DIR=%SCRIPT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%W in ('powershell -NoProfile -Command "Get-Date -UFormat '%%Y-W%%V'"') do set "WEEK_TAG=%%W"
set "LOGFILE=%LOG_DIR%\task_log_dashboard_test_2026_%WEEK_TAG%.txt"
if exist "%SCRIPT_DIR%\task_log_dashboard_test_2026.txt" del /q "%SCRIPT_DIR%\task_log_dashboard_test_2026.txt"
set "PYTHON_EXE=C:/Users/jtsao1/AppData/Local/Python/pythoncore-3.14-64/python.exe"
set "PY_SCRIPT=wireless_bug_dashboard_ips_hsd_jira.py"
rem Migration safety: keep scheduler in DML-only mode (no DROP/CREATE).
rem Use explicit year list to avoid single-year rolling refresh behavior.
set "PY_ARGS=--created-year 2026 --run-option 0 --db-append --no-menu --limit-ips 40 --limit-jira 40"

if not exist "%PYTHON_EXE%" (
  >>"%LOGFILE%" echo [ERROR] Python executable not found: %PYTHON_EXE%
  exit /b 9001
)

if not exist "%SCRIPT_DIR%\%PY_SCRIPT%" (
  >>"%LOGFILE%" echo [ERROR] Script not found: %SCRIPT_DIR%\%PY_SCRIPT%
  exit /b 9002
)

cd /d "%SCRIPT_DIR%" || (>>"%LOGFILE%" echo [ERROR] Failed to change to directory: %SCRIPT_DIR% & exit /b 1)

set "START_TIMESTAMP=%DATE% %TIME%"
>>"%LOGFILE%" echo ============================================================
>>"%LOGFILE%" echo [START] Task started at: %START_TIMESTAMP%
>>"%LOGFILE%" echo [INFO] Working Directory: %CD%
>>"%LOGFILE%" echo [INFO] Python Version:
"%PYTHON_EXE%" --version >>"%LOGFILE%" 2>&1
>>"%LOGFILE%" echo [INFO] Executing: %PY_SCRIPT% %PY_ARGS%
>>"%LOGFILE%" echo [INFO] --- Python output begin ---

:: ---- Take start time in centiseconds ----
for /f "tokens=1-4 delims=:." %%a in ("%time%") do (
  set /a T1=1%%a%%100*3600+1%%b%%100*60+1%%c%%100
  set /a T1=T1*100+1%%d%%100
)

"%PYTHON_EXE%" "%PY_SCRIPT%" %PY_ARGS% >>"%LOGFILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
>>"%LOGFILE%" echo [INFO] --- Python output end ---

:: ---- Take end time in centiseconds ----
for /f "tokens=1-4 delims=:." %%a in ("%time%") do (
  set /a T2=1%%a%%100*3600+1%%b%%100*60+1%%c%%100
  set /a T2=T2*100+1%%d%%100
)

:: ---- Handle midnight rollover ----
if !T2! LSS !T1! set /a T2=T2+8640000

set /a ELAPSED_CS=!T2!-!T1!
set /a ELAPSED_S=ELAPSED_CS/100
set /a ELAPSED_MS=(ELAPSED_CS%%100)*10

:: Format hh:mm:ss.mmm (approx)
set /a hh=ELAPSED_S/3600, remS=ELAPSED_S%%3600, mm=remS/60, ss=remS%%60
set "hh=0!hh!" & set "mm=0!mm!" & set "ss=0!ss!"
set "hh=!hh:~-2!" & set "mm=!mm:~-2!" & set "ss=!ss:~-2!"
set "ms=00!ELAPSED_MS!" & set "ms=!ms:~-3!"

>>"%LOGFILE%" echo [INFO] Elapsed: !hh!:!mm!:!ss!.!ms! (!ELAPSED_S!.!ELAPSED_MS! s)

set "END_TIMESTAMP=%DATE% %TIME%"
if %EXIT_CODE% EQU 0 (
  >>"%LOGFILE%" echo [SUCCESS] Script completed successfully with exit code: %EXIT_CODE%
  >>"%LOGFILE%" echo [SUCCESS] Data update finished at: %END_TIMESTAMP%
) else (
  >>"%LOGFILE%" echo [ERROR] Script failed with exit code: %EXIT_CODE%
  >>"%LOGFILE%" echo [ERROR] Task failed at: %END_TIMESTAMP%
)

>>"%LOGFILE%" echo [END] Task finished at: %END_TIMESTAMP%
>>"%LOGFILE%" echo ============================================================
>>"%LOGFILE%" echo.

endlocal
exit /b %EXIT_CODE%
