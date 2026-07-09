@echo off
setlocal

call "%~dp0jobs\03_offload_automation\run_offload_csv_watcher.bat" %*

endlocal