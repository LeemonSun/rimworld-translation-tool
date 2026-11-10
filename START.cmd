@echo off
cd /d "%~dp0"
if "%~1"=="" (
 python sol.py --help
) else (
 python sol.py %*
)
