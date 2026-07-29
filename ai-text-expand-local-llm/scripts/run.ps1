$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot "ahk\ai_text_expand.ahk"

# Detect Intel iGPU and ensure Ollama is running with the right env var.
# This handles the common case where Ollama was started by Windows before
# the user env var was set, causing it to fall back to CPU.
function Ensure-OllamaIntelGpu()
{
    try {
        $intelGpu = Get-CimInstance Win32_VideoController |
            Where-Object {
                ($_.Name -match "Intel") -and
                ($_.Name -match "Arc|Iris|UHD|HD|Graphics")
            } |
            Select-Object -First 1

        if (-not $intelGpu) {
            return
        }

        # Persist the env var so future sessions and child processes see it
        $alreadyPersisted = [Environment]::GetEnvironmentVariable("OLLAMA_IGPU_ENABLE", "User") -eq "1"
        $env:OLLAMA_IGPU_ENABLE = "1"
        if (-not $alreadyPersisted) {
            [Environment]::SetEnvironmentVariable("OLLAMA_IGPU_ENABLE", "1", "User")
        }

        $ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $ollama) {
            return
        }

        $ollamaRunning = $null -ne (Get-Process -Name "ollama" -ErrorAction SilentlyContinue)

        if (-not $ollamaRunning) {
            # Start Ollama now; it will inherit OLLAMA_IGPU_ENABLE from this session
            Write-Host "Starting Ollama with Intel GPU acceleration ($($intelGpu.Name))..."
            Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
            Start-Sleep -Milliseconds 1000
        } elseif (-not $alreadyPersisted) {
            # Ollama was already running but didn't have the env var —
            # restart it so it actually uses the iGPU
            Write-Host "Intel GPU detected ($($intelGpu.Name)). Restarting Ollama to enable GPU acceleration..."
            Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 800
            Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
            Start-Sleep -Milliseconds 1000
        }
    } catch {
        Write-Warning "Could not configure Intel GPU for Ollama: $_"
    }
}

Ensure-OllamaIntelGpu

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