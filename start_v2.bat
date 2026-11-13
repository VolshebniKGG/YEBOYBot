@echo off
cd /d "%~dp0"
if not exist .venv-v2\Scripts\python.exe (
 echo Run install_v2.bat first.
 pause
 exit /b 1
)
.venv-v2\Scripts\python.exe -m core_v2.doctor
if errorlevel 1 (
 pause
 exit /b 1
)
.venv-v2\Scripts\python.exe -m core_v2
pause
