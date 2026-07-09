$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "ahk\ai_text_expand.ahk"

$candidates = @(
    "AutoHotkey64",
    "AutoHotkey",
    "autohotkey",
    "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:LOCALAPPDATA\Programs\AutoHotkey\AutoHotkey64.exe",
    "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe",
    "$env:ProgramFiles\AutoHotkey\AutoHotkey64.exe"
)

$autoHotkey = $null
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
        $autoHotkey = $command.Source
        break
    }
    if (Test-Path $candidate) {
        $autoHotkey = $candidate
        break
    }
}

if (-not $autoHotkey) {
    throw "AutoHotkey v2 was not found. Install it with: winget install --id AutoHotkey.AutoHotkey --exact"
}

Get-CimInstance Win32_Process -Filter "name = 'AutoHotkey64.exe' or name = 'AutoHotkey.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($scriptPath) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

$quotedScriptPath = '"{0}"' -f $scriptPath
Start-Process -FilePath $autoHotkey -ArgumentList $quotedScriptPath
Write-Host "AI Text Expand is running. Use the AutoHotkey tray icon to exit."