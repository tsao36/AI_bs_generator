$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

# ── Step 1: Unit tests (no Ollama required) ───────────────────────────────────
# Run these BEFORE and AFTER any code change to catch regressions.
Write-Host ""
Write-Host "=== Unit Tests (language detection, no Ollama needed) ==="
& $python -m pytest tests/test_language_detection.py -v
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Error "Unit tests FAILED. Fix before proceeding."
    exit 1
}
Write-Host ""
Write-Host "Unit tests passed."

# ── Step 2: Live integration test (requires Ollama running) ──────────────────
Write-Host ""
Write-Host "=== Integration Test: English expansion ==="
$inputFile  = Join-Path $env:TEMP "ai_text_expand_test_input.txt"
$outputFile = Join-Path $env:TEMP "ai_text_expand_test_output.txt"

Set-Content -Path $inputFile -Value "Please follow up with the customer." -Encoding UTF8
& $python .\src\ai_text_expand\expand_text.py --input $inputFile --output $outputFile --config .\config.example.json
Write-Host "English output:"
Get-Content $outputFile

# ── Step 3: Traditional Chinese integration test ──────────────────────────────
Write-Host ""
Write-Host "=== Integration Test: Traditional Chinese expansion ==="
Set-Content -Path $inputFile -Value "祝你們永浴愛河，白頭偕老。" -Encoding UTF8
& $python .\src\ai_text_expand\expand_text.py --input $inputFile --output $outputFile --config .\config.example.json
Write-Host "Traditional Chinese output:"
Get-Content $outputFile

