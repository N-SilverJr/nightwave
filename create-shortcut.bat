@echo off
rem Adds "Nightwave" (with its icon) to your Desktop and Start menu.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" ( echo Run run.bat once first. & pause & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\create-shortcut.ps1"
pause
