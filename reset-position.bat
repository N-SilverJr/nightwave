@echo off
rem Use this if Nightwave ever ends up somewhere you cannot see it.
rem It moves the widget back to the bottom-right corner at its normal size.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" ( echo Run run.bat once first. & pause & exit /b 1 )
start "" ".venv\Scripts\pythonw.exe" main.py --reset
