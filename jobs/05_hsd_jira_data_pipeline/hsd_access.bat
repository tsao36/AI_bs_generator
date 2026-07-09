@echo off
setlocal

pushd "%~dp0" >nul

if "%~1"=="" (
	python HSD_access.py --csv-file hsd_export.csv --export-json output.json --log-level INFO
) else (
	python HSD_access.py %*
)

popd >nul