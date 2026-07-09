@echo off
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%" || (
  echo [ERROR] Failed to change directory to %SCRIPT_DIR%
  exit /b 1
)

set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if "%TS%"=="" set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOG_FILE=%LOG_DIR%\pptx_update_reminder_%TS%.log"

set "PYTHON_EXE=.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Python314\python.exe"

(
echo [INFO] ===== PPTX weekly reminder start =====
echo [INFO] Timestamp: %DATE% %TIME%
echo [INFO] Script dir: %SCRIPT_DIR%
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] Recipients: built-in list in send_pptx_update_reminder.py
echo [INFO] Mode: Graph email send
)>>"%LOG_FILE%"

echo [INFO] Running weekly PPTX reminder email script...
echo [INFO] Log file: %LOG_FILE%

"%PYTHON_EXE%" "send_pptx_update_reminder.py" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERROR] Weekly PPTX reminder batch failed.
  echo [ERROR] Weekly PPTX reminder batch failed.>>"%LOG_FILE%"
  echo [INFO] Exit code: 1>>"%LOG_FILE%"
  echo [INFO] ===== PPTX weekly reminder end =====>>"%LOG_FILE%"
  exit /b 1
)

echo [OK] Weekly PPTX reminder email sent.
echo [OK] Weekly PPTX reminder email sent.>>"%LOG_FILE%"
echo [INFO] Exit code: 0>>"%LOG_FILE%"
echo [INFO] ===== PPTX weekly reminder end =====>>"%LOG_FILE%"
exit /b 0
