@echo off
rem Double-click to start Scheme Saathi. Extra options are passed on, e.g. start.bat -NoWeb
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
