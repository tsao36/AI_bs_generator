$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$version = "0.1.0"
$packageName = "AI-Text-Expand-Local-LLM-$version"
$distDir = Join-Path $projectRoot "dist"
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) "$packageName-package"
$stagingDir = Join-Path $stagingRoot $packageName
$zipPath = Join-Path $distDir "$packageName.zip"

if (Test-Path $stagingRoot) {
    Remove-Item $stagingRoot -Recurse -Force
}
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

New-Item -ItemType Directory -Path $stagingDir | Out-Null

$itemsToPackage = @(
    "ahk",
    "scripts",
    "src",
    "config.example.json",
    "pyproject.toml",
    "requirements.txt",
    "README.md"
)

foreach ($item in $itemsToPackage) {
    $source = Join-Path $projectRoot $item
    if (-not (Test-Path $source)) {
        throw "Required package item not found: $source"
    }

    Copy-Item $source -Destination $stagingDir -Recurse -Force
}

Get-ChildItem $stagingDir -Directory -Recurse -Force |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache") } |
    Remove-Item -Recurse -Force

Get-ChildItem $stagingDir -File -Recurse -Force |
    Where-Object { $_.Name -like "*.pyc" -or $_.Name -like "*.pyo" } |
    Remove-Item -Force

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path $stagingDir -DestinationPath $zipPath
Remove-Item $stagingRoot -Recurse -Force

Write-Host "Package created: $zipPath"
Write-Host "Share this zip with users. They should extract it, then run scripts\install.ps1 and scripts\run.ps1."