@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv-v2
if errorlevel 1 exit /b 1
.venv-v2\Scripts\python.exe -m pip install -r core_v2\requirements.txt
if errorlevel 1 exit /b 1
if not exist .env.v2 copy .env.v2.example .env.v2 >nul
echo Edit .env.v2, then run start_v2.bat
pause
