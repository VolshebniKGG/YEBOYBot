@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "SETUP_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%SETUP_PS%" goto missing
if not exist "%~dp0tools\windows_setup.ps1" goto missing
set "SETUP_MODE=Ensure"
if "%~1"=="--repair" set "SETUP_MODE=Repair"
"%SETUP_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_setup.ps1" -Mode %SETUP_MODE%
exit /b %ERRORLEVEL%
:missing
echo Потрібні всі файли проєкту та Windows PowerShell 5.1. Повторно розпакуйте архів.
echo Python 3.12: https://www.python.org/downloads/
exit /b 1
