@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\weekly_tuning_prepare_%TS%.log"

REM Recommended scheduler command (no arguments):
REM   .\run_weekly_tuning_and_prepare_labeling.bat
REM Recommended defaults:
REM   threshold=0.45, topN=all

set "THRESHOLD=0.45"
set "TOPN=all"
if not "%~1"=="" set "THRESHOLD=%~1"
if not "%~2"=="" set "TOPN=%~2"

(
echo [INFO] ===== Weekly tuning + prepare start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Parameters: threshold=%THRESHOLD% topN=%TOPN%
)>>"%LOG_FILE%"

echo [INFO] Monday recommended command: .\run_weekly_tuning_and_prepare_labeling.bat
echo [INFO] Effective parameters: threshold=%THRESHOLD% topN=%TOPN%
echo [INFO] Log file: %LOG_FILE%

echo [STEP 1/2] Weekly tuning
echo [STEP 1/3] Weekly tuning>>"%LOG_FILE%"
call .\run_weekly_category_tuning.bat %THRESHOLD% >>"%LOG_FILE%" 2>&1
set "STEP1_EXIT=!ERRORLEVEL!"
if not "!STEP1_EXIT!"=="0" (
	echo [ERROR] Weekly tuning failed. exit=!STEP1_EXIT!
	echo [ERROR] Weekly tuning failed. exit=!STEP1_EXIT!>>"%LOG_FILE%"
	echo [INFO] Exit code: !STEP1_EXIT!>>"%LOG_FILE%"
	echo [INFO] ===== Weekly tuning + prepare end =====>>"%LOG_FILE%"
	exit /b !STEP1_EXIT!
)

echo [STEP 2/2] Prepare labeling template
echo [STEP 2/3] Prepare labeling template>>"%LOG_FILE%"
call .\prepare_weekly_labeling_template.bat %TOPN% >>"%LOG_FILE%" 2>&1
set "STEP2_EXIT=!ERRORLEVEL!"
if not "!STEP2_EXIT!"=="0" (
	echo [ERROR] Prepare labeling template failed. exit=!STEP2_EXIT!
	echo [ERROR] Prepare labeling template failed. exit=!STEP2_EXIT!>>"%LOG_FILE%"
	echo [INFO] Exit code: !STEP2_EXIT!>>"%LOG_FILE%"
	echo [INFO] ===== Weekly tuning + prepare end =====>>"%LOG_FILE%"
	exit /b !STEP2_EXIT!
)

echo [STEP 3/3] Send labeling reminders for pending human_category
echo [STEP 3/3] Send labeling reminders for pending human_category>>"%LOG_FILE%"
call .\run_labeling_reminder_daily.bat >>"%LOG_FILE%" 2>&1
set "STEP3_EXIT=!ERRORLEVEL!"
if not "!STEP3_EXIT!"=="0" (
	echo [ERROR] Send labeling reminders failed. exit=!STEP3_EXIT!
	echo [ERROR] Send labeling reminders failed. exit=!STEP3_EXIT!>>"%LOG_FILE%"
	echo [INFO] Exit code: !STEP3_EXIT!>>"%LOG_FILE%"
	echo [INFO] ===== Weekly tuning + prepare end =====>>"%LOG_FILE%"
	exit /b !STEP3_EXIT!
)

echo [OK] Weekly tuning + labeling preparation + reminder complete.
echo [OK] Weekly tuning + labeling preparation + reminder complete.>>"%LOG_FILE%"
echo [INFO] Verify in this log: line '[OK] Targeted supplement rows: N' from prepare_weekly_labeling_template.py>>"%LOG_FILE%"
echo [INFO] Exit code: 0>>"%LOG_FILE%"
echo [INFO] ===== Weekly tuning + prepare end =====>>"%LOG_FILE%"
exit /b 0
