

@echo off
:: Перех?д до директор?ї проекту
cd /d "E:\Discord Bot\Bot"

:: Перев?рка наявност? Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    pause
    exit /b
)

:: Перев?рка встановлених залежностей
pip freeze > requirements.txt
fc requirements.txt requirements.txt >nul
if %ERRORLEVEL% neq 0 (
    pip install -r requirements.txt
)

:: Запуск бота
python run.py

pause


