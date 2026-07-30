# Called by .git/hooks/pre-commit whenever a commit is made.
# If source files inside ai-text-expand-local-llm/ are staged,
# automatically bumps the patch version and rebuilds the dist package.

$ErrorActionPreference = "Stop"

# Derive absolute paths from this script's own location
$projectDir    = Split-Path -Parent $PSScriptRoot          # ai-text-expand-local-llm/
$repoRoot      = Split-Path -Parent $projectDir             # repo root

# Paths relative to repo root (used by git commands)
$pyprojectRel  = "ai-text-expand-local-llm/pyproject.toml"
$packageRel    = "ai-text-expand-local-llm/scripts/package.ps1"
$distPrefixRel = "ai-text-expand-local-llm/dist/"

# Absolute paths (used for file I/O)
$pyprojectAbs  = Join-Path $projectDir "pyproject.toml"
$packageAbs    = Join-Path $projectDir "scripts\package.ps1"

# ── 1. Check whether any source files (not dist/, not version files) are staged ──
Set-Location $repoRoot
$staged = git diff --cached --name-only
$sourceChanged = @($staged | Where-Object {
    $_ -match "^ai-text-expand-local-llm/" -and
    $_ -notmatch "^ai-text-expand-local-llm/dist/" -and
    $_ -ne $pyprojectRel -and
    $_ -ne $packageRel
})

if ($sourceChanged.Count -eq 0) {
    exit 0   # nothing to do
}

# ── 2. Read and bump the patch version ──
$pyprojectContent = Get-Content $pyprojectAbs -Raw
$versionMatch = [regex]::Match($pyprojectContent, 'version = "(\d+)\.(\d+)\.(\d+)"')
if (-not $versionMatch.Success) {
    Write-Host "[pre-commit] Could not parse version from pyproject.toml — skipping auto-package."
    exit 0
}
$newVersion = "$($versionMatch.Groups[1].Value).$($versionMatch.Groups[2].Value).$([int]$versionMatch.Groups[3].Value + 1)"
Write-Host "[pre-commit] Bumping version to $newVersion and building package..."

# ── 3. Write new version into both files ──
$pyprojectContent = $pyprojectContent -replace 'version = "\d+\.\d+\.\d+"', "version = `"$newVersion`""
[System.IO.File]::WriteAllText($pyprojectAbs, $pyprojectContent)

$packageContent = Get-Content $packageAbs -Raw
$packageContent = $packageContent -replace '\$version = "\d+\.\d+\.\d+"', "`$version = `"$newVersion`""
[System.IO.File]::WriteAllText($packageAbs, $packageContent)

# ── 4. Build the dist zip ──
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $packageAbs
if ($LASTEXITCODE -ne 0) {
    Write-Host "[pre-commit] Package build failed — aborting commit."
    exit 1
}

# ── 5. Stage the generated artefacts ──
git add $pyprojectRel
git add $packageRel
git add "$($distPrefixRel)AITextExpandLocalLLM-v$newVersion.zip"

Write-Host "[pre-commit] v$newVersion packaged and staged."
exit 0
