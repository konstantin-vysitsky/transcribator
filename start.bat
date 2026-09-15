@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating venv...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" "%~dp0start_background.vbs"
