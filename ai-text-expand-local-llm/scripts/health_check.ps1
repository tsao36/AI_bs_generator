$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "ahk\ai_text_expand.ahk"

$proc = Get-CimInstance Win32_Process -Filter "name = 'AutoHotkey64.exe' or name = 'AutoHotkey.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($scriptPath) } |
    Select-Object -First 1

if ($proc) {
    Write-Host "OK: AI Text Expand helper is running (PID=$($proc.ProcessId))."
    exit 0
}

Write-Host "WARN: AI Text Expand helper is not running. Starting it now..."
& (Join-Path $projectRoot "scripts\run.ps1")

Start-Sleep -Milliseconds 300

$proc2 = Get-CimInstance Win32_Process -Filter "name = 'AutoHotkey64.exe' or name = 'AutoHotkey.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($scriptPath) } |
    Select-Object -First 1

if ($proc2) {
    Write-Host "OK: Helper started successfully (PID=$($proc2.ProcessId))."
    exit 0
}

Write-Host "ERROR: Helper did not start. Run scripts\run.ps1 manually and check for AutoHotkey install issues."
exit 1
