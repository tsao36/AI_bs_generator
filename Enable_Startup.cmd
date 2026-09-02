@echo off
setlocal
cd /d "%~dp0"

echo Enabling startup for AI Text Expand Local LLM...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\enable_startup.ps1"

if errorlevel 1 (
    echo.
    echo Startup setup failed. See the error message above.
    pause
    exit /b 1
)

echo.
echo Startup setup complete.
pause