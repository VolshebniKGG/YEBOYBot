@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PY=%ROOT%.venv-v2\Scripts\python.exe"
where py >nul 2>nul
if not exist "%PY%" (
  echo Installing YEBOYBot Core V2 environment...
  call "%ROOT%install_v2.bat"
  if errorlevel 1 (echo Installation failed.& pause& exit /b 1)
)
"%PY%" -m tools.launcher
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
