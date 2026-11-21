@echo off
cd /d "%~dp0"
python scripts\open_workspace.py --admin
if errorlevel 1 pause
