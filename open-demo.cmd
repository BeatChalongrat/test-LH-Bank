@echo off
setlocal
cd /d "%~dp0"
python scripts\hr_demo.py tour
if errorlevel 1 pause
