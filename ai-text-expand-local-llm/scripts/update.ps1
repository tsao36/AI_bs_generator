param(
    [string]$CurrentVersion = "",
    [string]$Proxy = "http://proxy-dmz.intel.com:912",
    [string]$InstallDir = "$env:LOCALAPPDATA\AITextExpandLocalLLM"
)

$ErrorActionPreference = "Stop"
$repoApiUrl = "https://api.github.com/repos/tsao36/AI_bs_generator/contents/ai-text-expand-local-llm/dist"

function Invoke-ApiRequest($url) {
    $headers = @{ "User-Agent" = "AITextExpand-Updater" }
    try {
        return Invoke-RestMethod -Uri $url -Headers $headers -Proxy $Proxy -ErrorAction Stop
    } catch {
        Write-Host "Proxy failed, retrying without proxy..."
        return Invoke-RestMethod -Uri $url -Headers $headers -ErrorAction Stop
    }
}

function Invoke-FileDownload($url, $outFile) {
    $headers = @{ "User-Agent" = "AITextExpand-Updater" }
    try {
        Invoke-WebRequest -Uri $url -OutFile $outFile -Headers $headers -Proxy $Proxy -ErrorAction Stop
    } catch {
        Write-Host "Proxy failed, retrying without proxy..."
        Invoke-WebRequest -Uri $url -OutFile $outFile -Headers $headers -ErrorAction Stop
    }
}

Write-Host "Fetching available versions from GitHub..."
$contents = Invoke-ApiRequest $repoApiUrl

$zipFiles = @($contents | Where-Object { $_.name -match "^AITextExpandLocalLLM-v(\d+\.\d+\.\d+)\.zip$" })
if ($zipFiles.Count -eq 0) {
    Write-Error "No release packages found in GitHub dist folder."
    exit 1
}

# Sort by parsed version number descending to get the true latest
$latest = $zipFiles | Sort-Object {
    if ($_.name -match "v(\d+)\.(\d+)\.(\d+)") {
        [int]$Matches[1] * 10000 + [int]$Matches[2] * 100 + [int]$Matches[3]
    } else { 0 }
} -Descending | Select-Object -First 1

$latestVersion = if ($latest.name -match "v(\d+\.\d+\.\d+)") { $Matches[1] } else { "unknown" }

if ($CurrentVersion -ne "" -and $CurrentVersion -eq $latestVersion) {
    Write-Host "Already up to date (v$CurrentVersion). No update needed."
    exit 0
}

if ($CurrentVersion -ne "") {
    Write-Host "Update available: v$CurrentVersion -> v$latestVersion"
} else {
    Write-Host "Latest version: v$latestVersion"
}

# Download to a timestamped temp folder
$tempDir = Join-Path $env:TEMP "AITextExpand_update_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Path $tempDir | Out-Null
$zipPath = Join-Path $tempDir $latest.name

Write-Host "Downloading $($latest.name)..."
Invoke-FileDownload $latest.download_url $zipPath

Write-Host "Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $tempDir -Force

$extractedDir = Get-ChildItem $tempDir -Directory |
    Where-Object { $_.Name -like "AITextExpandLocalLLM-v*" } |
    Select-Object -First 1

if (-not $extractedDir) {
    Write-Error "Could not find extracted package folder in $tempDir"
    exit 1
}

Write-Host "Installing v$latestVersion to $InstallDir ..."
$installScript = Join-Path $extractedDir.FullName "scripts\install.ps1"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript `
    -InstallDir $InstallDir `
    -SkipModelPull `
    -SkipStart

$installExit = $LASTEXITCODE

# Clean up temp files regardless of outcome
Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue

if ($installExit -ne 0) {
    Write-Error "Installer exited with code $installExit"
    exit $installExit
}

Write-Host ""
Write-Host "Update complete! Installed v$latestVersion"
Write-Host "Close this window and relaunch AI Text Expand to use the new version."
