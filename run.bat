

@echo off
:: ������� �� �������� �������
cd /d "E:\Discord Bot\Bot"

:: �������� �������� Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    pause
    exit /b
)

:: �������� ������������ �����������
pip freeze > requirements.txt
fc requirements.txt requirements.txt >nul
if %ERRORLEVEL% neq 0 (
    pip install -r requirements.txt
)

:: ������ ����
python run.py

pause


