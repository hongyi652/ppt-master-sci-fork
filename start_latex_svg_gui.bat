@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_CMD="
python3 -c "print('OK')" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python3"

if not defined PYTHON_CMD (
  python -c "print('OK')" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  py -3 -c "print('OK')" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  echo [ERROR] Python was not found. Install Python 3 and try again.
  pause
  exit /b 1
)

%PYTHON_CMD% skills\ppt-master\scripts\latex_svg_gui.py --open
pause
