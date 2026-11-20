@echo off
rem Runs Nightwave with a console + browser dev tools so you can see errors.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Run run.bat once first. & pause & exit /b 1 )
".venv\Scripts\python.exe" main.py --debug
echo.
echo Log file: %APPDATA%\Nightwave\nightwave.log
pause
