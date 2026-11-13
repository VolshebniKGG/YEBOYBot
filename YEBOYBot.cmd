@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "RC="
set "ERRORLEVEL="
chcp 65001 >nul
set "PYTHONUTF8=1"
title YEBOYBot - Control Center
echo.
echo   YEBOYBot / Завантаження панелі керування...
echo.
cd /d "%~dp0"
if errorlevel 1 goto bad_directory
set "SETUP_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%SETUP_PS%" goto no_powershell
if not exist "%~dp0tools\windows_setup.ps1" goto incomplete
"%SETUP_PS%" -NoLogo -NoProfile -File "%~dp0tools\windows_setup.ps1" -Mode Launcher
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo [!] Якщо повідомлення вище стосується політики PowerShell, зверніться до адміністратора ПК.
goto failed
:bad_directory
echo [ПОМИЛКА] Не вдалося відкрити папку YEBOYBot. Перевірте доступ або розпакуйте ZIP на локальний диск.
goto failed
:no_powershell
echo Не знайдено вбудований Windows PowerShell 5.1. Перевірте компоненти та правила Windows.
goto failed
:incomplete
echo Розпакуйте весь архів YEBOYBot: потрібен tools\windows_setup.ps1.
:failed
if not defined RC set "RC=1"
echo Запуск не завершено (код %RC%). Дані й конфігурація бота не видаляються.
pause
exit /b %RC%
