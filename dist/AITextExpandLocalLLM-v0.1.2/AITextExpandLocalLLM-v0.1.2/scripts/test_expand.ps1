$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$inputFile = Join-Path $env:TEMP "ai_text_expand_test_input.txt"
$outputFile = Join-Path $env:TEMP "ai_text_expand_test_output.txt"

Set-Content -Path $inputFile -Value "Please follow up with the customer." -Encoding UTF8

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python .\src\ai_text_expand\expand_text.py --input $inputFile --output $outputFile --config .\config.example.json

Write-Host "Expanded output:"
Get-Content $outputFile
