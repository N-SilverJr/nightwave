@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up Nightwave. This takes a minute or two...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Could not create a virtual environment. Is Python 3.10+ installed and on PATH?
    pause & exit /b 1
  )
)
rem (re)install packages whenever requirements.txt changes
fc /b requirements.txt ".venv\installed-requirements.txt" >nul 2>&1
if errorlevel 1 (
  echo Installing / updating Nightwave's packages...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Installing the requirements failed - see the messages above.
    pause & exit /b 1
  )
  copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)
start "" ".venv\Scripts\pythonw.exe" main.py
