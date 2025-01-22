

@echo off
:: Перехід до директорії проекту
cd /d "E:\Discord Bot\Bot"

:: Перевірка наявності Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    pause
    exit /b
)

:: Перевірка встановлених залежностей
pip freeze > requirements.txt
fc requirements.txt requirements.txt >nul
if %ERRORLEVEL% neq 0 (
    pip install -r requirements.txt
)

:: Запуск бота
python run.py

pause


