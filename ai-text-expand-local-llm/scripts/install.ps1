param(
    [string]$Model,
    [switch]$SkipWingetInstall,
    [switch]$SkipModelPull,
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

function Update-ProcessPath()
{
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Install-WithWinget($packageId, $displayName)
{
    if ($SkipWingetInstall) {
        throw "$displayName was not found. Install it manually or rerun without -SkipWingetInstall."
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget was not found. Install $displayName manually, then rerun this script."
    }

    Write-Host "Installing $displayName with winget..."
    & winget install --id $packageId --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install $displayName with winget."
    }

    Update-ProcessPath
}

function Get-PythonCommand()
{
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    return $null
}

function Get-AutoHotkeyCommand()
{
    $candidates = @(
        "AutoHotkey64",
        "AutoHotkey",
        "$env:LOCALAPPDATA\Programs\AutoHotkey\v2\AutoHotkey64.exe",
        "$env:LOCALAPPDATA\Programs\AutoHotkey\AutoHotkey64.exe",
        "$env:ProgramFiles\AutoHotkey\v2\AutoHotkey64.exe",
        "$env:ProgramFiles\AutoHotkey\AutoHotkey64.exe"
    )

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

function Get-ConfiguredModel($configPath)
{
    if (-not $Model -and (Test-Path $configPath)) {
        $config = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($config.LOCAL_LLM_MODEL) {
            return $config.LOCAL_LLM_MODEL
        }
    }

    if ($Model) {
        return $Model
    }

    return "llama3.1:8b-instruct-q4_K_M"
}

function Test-OllamaApi($baseUrl)
{
    try {
        Invoke-RestMethod -Uri "$baseUrl/api/tags" -Method Get -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-OllamaIfNeeded($baseUrl)
{
    if (Test-OllamaApi $baseUrl) {
        Write-Host "Ollama is already running."
        return
    }

    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        throw "Ollama command was not found after install. Restart PowerShell, then rerun this script."
    }

    Write-Host "Starting Ollama..."
    Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden

    foreach ($attempt in 1..20) {
        Start-Sleep -Milliseconds 500
        if (Test-OllamaApi $baseUrl) {
            Write-Host "Ollama is running."
            return
        }
    }

    throw "Ollama did not respond at $baseUrl. Start Ollama manually, then rerun this script."
}

$configPath = ".\config.example.json"
$baseUrl = "http://127.0.0.1:11434"
if (Test-Path $configPath) {
    $config = Get-Content $configPath -Raw | ConvertFrom-Json
    if ($config.OLLAMA_BASE_URL) {
        $baseUrl = $config.OLLAMA_BASE_URL
    }
}

$python = Get-PythonCommand
if (-not $python) {
    Install-WithWinget "Python.Python.3.12" "Python 3.12"
    $python = Get-PythonCommand
}
if (-not $python) {
    throw "Python was not found after install. Restart PowerShell, then rerun this script."
}

if ($python -like "*\py.exe") {
    & $python -3 -m venv .venv
} else {
    & $python -m venv .venv
}

$requirements = Get-Content .\requirements.txt | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith("#") }
if ($requirements) {
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python requirements."
    }
}

if (-not (Get-AutoHotkeyCommand)) {
    Install-WithWinget "AutoHotkey.AutoHotkey" "AutoHotkey v2"
}
if (-not (Get-AutoHotkeyCommand)) {
    throw "AutoHotkey v2 was not found after install. Restart PowerShell, then rerun this script."
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Install-WithWinget "Ollama.Ollama" "Ollama"
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama was not found after install. Restart PowerShell, then rerun this script."
}

Start-OllamaIfNeeded $baseUrl

$modelName = Get-ConfiguredModel $configPath
if ($SkipModelPull) {
    Write-Host "Skipping model pull. Required model: $modelName"
} else {
    Write-Host "Pulling Ollama model: $modelName"
    & ollama pull $modelName
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull Ollama model: $modelName"
    }
}

Write-Host "Install complete."
if ($SkipStart) {
    Write-Host "Start skipped. Run scripts\run.ps1 when you are ready to start the AutoHotkey helper."
    exit 0
}

Write-Host "Starting AI Text Expand..."
& (Join-Path (Get-Location) "scripts\run.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Install completed, but AI Text Expand did not start. Run scripts\run.ps1 manually and check the error message."
}

Write-Host "AI Text Expand is installed and running."
