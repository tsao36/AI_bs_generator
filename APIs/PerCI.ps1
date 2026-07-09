param (
    [switch]$NoTests
)

if ($null -eq $Env:VIRTUAL_ENV) {
    Write-Host "ERROR: Must be run from a Python Virtual Environment!" -ForegroundColor Red
    Exit 1
}

python .\PerCI\file_validator.py --target-dir $(Get-Location) --config-file .\PerCI\config.json $(If ($NoTests) {"--no-tests"})