@echo off
REM ASCII only, and no goto / labels - see install.bat for the reasons.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "VENV_PY=..\backend\.venv\Scripts\python.exe"

if exist "%VENV_PY%" (
  "%VENV_PY%" serve.py
  if errorlevel 1 pause
) else (
  echo.
  echo   [ERROR] Not installed yet. Please run install.bat first.
  echo.
  pause
)
