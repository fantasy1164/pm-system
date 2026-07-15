@echo off
REM ASCII only on purpose: Windows parses .bat with the system ANSI codepage
REM (cp950 on zh-TW), so Chinese text here would turn into mojibake.
REM All user-facing messages live in install.py instead.
REM
REM No goto / no labels on purpose: a .bat saved with LF line endings can fail
REM to resolve labels on some Windows versions. Staying label-free makes this
REM script work regardless of how git checked it out.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Prefer the py launcher; plain "python" may be a Microsoft Store stub.
set "PYEXE="
py -3 -c "pass" >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"
python -c "pass" >nul 2>&1
if not errorlevel 1 if not defined PYEXE set "PYEXE=python"

if defined PYEXE (
  %PYEXE% install.py
) else (
  echo.
  echo   [ERROR] Python not found.
  echo.
  echo   Install Python 3.12 from https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add Python to PATH" during installation.
  echo.
)

pause
