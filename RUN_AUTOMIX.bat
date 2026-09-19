@echo off
cd /d "%~dp0"
python automix_app.py
if errorlevel 1 pause
