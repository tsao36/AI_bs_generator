@echo off
setlocal
cd /d "%~dp0"

echo Installing AI Text Expand Local LLM...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"

if errorlevel 1 (
    echo.
    echo Install failed. See the error message above.
    pause
    exit /b 1
)

echo.
echo Install complete. Double-click Run.cmd to start AI Text Expand.
pause