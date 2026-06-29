@echo off
REM Launch the Flash timer overlay. Installs dependencies on first run.
cd /d "%~dp0"

REM Pick a Python launcher: prefer "py", fall back to "python".
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON (
    where python >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
    echo Python was not found on PATH.
    echo Install Python 3.12+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

%PYTHON% -m pip install -r requirements.txt
%PYTHON% main.py
pause
