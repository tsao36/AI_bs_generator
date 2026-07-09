@echo on
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "LOG_DIR=%SCRIPT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%W in ('powershell -NoProfile -Command "Get-Date -UFormat '%%Y-W%%V'"') do set "WEEK_TAG=%%W"
set "LOGFILE=%LOG_DIR%\task_log_dashboard_%WEEK_TAG%.txt"
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
rem Allow additive schema updates (ADD COLUMN) for new DB fields.
rem Daily mode: run main pipeline only. One-time backfill is intentionally excluded.
rem Team member source file is jobs\04_wireless_bug_dashboard\cfe_team_member.json.
rem Note: this command line still passes --hsd-owner-filter explicitly, which overrides default team-list loading.
set "PY_ARGS=--created-year 2025,2026 --hsd-query-id 16021056445 --hsd-limit 500 --hsd-owner-filter yaochien,timdaway,szchen,fang,kj,kjfang,jtsao1,yuweich1,frankfcy,brentonw,chenmatt,caizhiqi,flee5,wesleyku,bingyues,jzou6,chuchar1 --run-option 4 --db-append --db-batch --allow-ddl --no-menu"

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

:: ---- Take start time in milliseconds (robust) ----
for /f %%i in ('powershell -NoProfile -Command "[int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"') do set "T1=%%i"

"%PYTHON_EXE%" "%PY_SCRIPT%" %PY_ARGS% >>"%LOGFILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

>>"%LOGFILE%" echo [INFO] --- Python output end ---

:: ---- Take end time in milliseconds ----
for /f %%i in ('powershell -NoProfile -Command "[int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"') do set "T2=%%i"

for /f %%j in ('powershell -NoProfile -Command "Write-Output (%T2% - %T1%)"') do set "ELAPSED_MS=%%j"
for /f %%k in ('powershell -NoProfile -Command "Write-Output ([math]::Floor(%ELAPSED_MS%/1000))"') do set "ELAPSED_S=%%k"

:: Format hh:mm:ss.mmm
set /a hh=ELAPSED_S/3600, remS=ELAPSED_S%%3600, mm=remS/60, ss=remS%%60
set "hh=0!hh!" & set "mm=0!mm!" & set "ss=0!ss!"
set "hh=!hh:~-2!" & set "mm=!mm:~-2!" & set "ss=!ss:~-2!"
set "ms=00!ELAPSED_MS!" & set "ms=!ms:~-3!"

>>"%LOGFILE%" echo [INFO] Elapsed: !hh!:!mm!:!ss!.!ms! (!ELAPSED_S! s)

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
