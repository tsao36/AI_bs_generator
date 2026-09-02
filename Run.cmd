@echo off
setlocal
cd /d "%~dp0"

echo Starting AI Text Expand Local LLM...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1"

if errorlevel 1 (
    echo.
    echo Start failed. See the error message above.
    pause
    exit /b 1
)

echo.
echo AI Text Expand is running. You can close this window.
pause