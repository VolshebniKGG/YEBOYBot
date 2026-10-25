@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
py -3.12 -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>nul
if not errorlevel 1 goto use_launcher
python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>nul
if not errorlevel 1 goto use_python
echo Потрібен Python 3.12. Завантажте з https://www.python.org/downloads/
echo Під час встановлення увімкніть Python Launcher або Add python.exe to PATH.
echo Після встановлення повторно відкрийте YEBOYBot.cmd.
exit /b 1
:use_launcher
py -3.12 -m tools.launcher.bootstrap %*
exit /b %ERRORLEVEL%
:use_python
python -m tools.launcher.bootstrap %*
exit /b %ERRORLEVEL%
