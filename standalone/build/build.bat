@echo off
rem ASCII only - Windows parses .bat with the ANSI codepage.
rem All Chinese messages live in build.py (same convention as install.bat).
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python not found. Install Python 3.9+ from python.org
  echo and check "Add Python to PATH" during installation.
  echo.
  pause
  exit /b 1
)

python build.py
pause
