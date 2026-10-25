@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PY=%ROOT%.venv-v2\Scripts\python.exe"
if exist "%PY%" goto menu
call "%ROOT%install_v2.bat"
if errorlevel 1 goto failed
:menu
"%PY%" -c "import sys, discord, sqlalchemy, aiosqlite, asyncpg, dotenv; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>nul
if errorlevel 1 goto repair
"%PY%" -m tools.launcher
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
:repair
echo Середовище неповне або несумісне. Спроба відновлення залежностей...
call "%ROOT%install_v2.bat" --repair
if errorlevel 1 goto failed
goto menu
:failed
echo Встановлення не завершено. Виправте помилку вище та повторіть запуск.
pause
exit /b 1
