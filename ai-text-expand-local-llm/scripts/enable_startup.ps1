$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$runScript = Join-Path $projectRoot "scripts\run.ps1"
if (-not (Test-Path $runScript)) {
    throw "run.ps1 not found: $runScript"
}

$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
if (-not (Test-Path $startupDir)) {
    New-Item -ItemType Directory -Path $startupDir | Out-Null
}

$shortcutPath = Join-Path $startupDir "AI Text Expand Local LLM.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Start AI Text Expand Local LLM helper"
$shortcut.Save()

Write-Host "Startup shortcut created: $shortcutPath"
Write-Host "The helper will auto-start after next sign-in."
