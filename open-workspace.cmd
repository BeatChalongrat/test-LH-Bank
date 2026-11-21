@echo off
cd /d "%~dp0"
python scripts\open_workspace.py
if errorlevel 1 pause
