@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if errorlevel 1 goto failed
set "SETUP_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%SETUP_PS%" goto no_powershell
if not exist "%~dp0tools\windows_setup.ps1" goto incomplete
"%SETUP_PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows_setup.ps1" -Mode Launcher
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
:no_powershell
echo Не знайдено вбудований Windows PowerShell 5.1. Перевірте компоненти та правила Windows.
goto failed
:incomplete
echo Розпакуйте весь архів YEBOYBot: потрібен tools\windows_setup.ps1.
:failed
echo Запуск не завершено. Дані й конфігурація бота не видаляються.
pause
exit /b 1
