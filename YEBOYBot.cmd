@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
title YEBOYBot - Control Center
echo.
echo   YEBOYBot / Завантаження панелі керування...
echo.
cd /d "%~dp0"
if errorlevel 1 goto bad_location
set "SETUP_PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%SETUP_PS%" goto no_powershell
if not exist "%~dp0tools\windows_setup.ps1" goto incomplete
"%SETUP_PS%" -NoLogo -NoProfile -File "%~dp0tools\windows_setup.ps1" -Mode Launcher
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo [ПОМИЛКА] Панель завершилася з кодом %RC%. Подробиці наведено вище.
if "%RC%"=="1" echo Якщо PowerShell повідомив про заборону сценарію, зверніться до адміністратора ПК.
if "%RC%"=="5" echo Windows відмовила у запуску PowerShell або сценарію. Перевірте Журнал захисту.
if "%RC%"=="577" echo Windows відхилила перевірку підпису. Зверніться до адміністратора ПК.
if "%RC%"=="1260" echo Windows заборонила виконання політикою. Зверніться до адміністратора ПК.
pause
exit /b %RC%
:no_powershell
echo Не знайдено вбудований Windows PowerShell 5.1. Перевірте компоненти та правила Windows.
goto failed
:bad_location
echo Не вдалося відкрити папку проєкту. Розпакуйте повний ZIP у доступну локальну папку.
goto failed
:incomplete
echo Розпакуйте весь архів YEBOYBot: потрібен tools\windows_setup.ps1.
:failed
echo Запуск не завершено. Дані й конфігурація бота не видаляються.
pause
exit /b 1
