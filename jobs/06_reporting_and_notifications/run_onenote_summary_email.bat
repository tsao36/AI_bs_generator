@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
    echo [ERROR] Failed to change directory to %SCRIPT_DIR%
    exit /b 1
)

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\onenote_summary_email_%TS%.log"
set "MODE_ARG=--send-email"

(
echo [INFO] ===== OneNote summary email start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Current dir: %CD%
echo [INFO] Command: python Meeting_agenda_OneNote.py %MODE_ARG% %*
)>>"%LOG_FILE%"

where python >>"%LOG_FILE%" 2>&1
python Meeting_agenda_OneNote.py %MODE_ARG% %* >>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

(
echo [INFO] Exit code: %EXIT_CODE%
echo [INFO] ===== OneNote summary email end =====
)>>"%LOG_FILE%"

exit /b %EXIT_CODE%
