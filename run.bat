@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
py -3.12 -I -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>nul
if not errorlevel 1 goto pylauncher
python -I -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>nul
if not errorlevel 1 goto pythonpath
echo Потрібен Python 3.12 з https://www.python.org/downloads/windows/
echo Під час встановлення увімкніть Python Launcher. Потім повторіть запуск.
pause
exit /b 1
:pylauncher
py -3.12 -I "%~dp0tools\legacy_launcher.py"
goto finished
:pythonpath
python -I "%~dp0tools\legacy_launcher.py"
:finished
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
