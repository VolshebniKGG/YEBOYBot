@ECHO off
CHCP 866 > NUL  & REM Встановлення кодування CP866 для коректного в?дображення кирилиц?

:: Перех?д у папку з? скриптом
CD /d "%~dp0"

:: Перев?ряємо, чи прихован? розширення файл?в
SET KEY_NAME="HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
SET VALUE_NAME=HideFileExt

FOR /F "usebackq tokens=1-3" %%A IN (`REG QUERY %KEY_NAME% /v %VALUE_NAME% 2^>nul`) DO (
    SET ValueValue=%%C
)

IF x%ValueValue:0x0=%==x%ValueValue% (
    ECHO В?дображення розширень файл?в...
    REG ADD HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced /v HideFileExt /t REG_DWORD /d 0 /f > NUL
)

:: Перев?рка наявност? Python
IF EXIST %SYSTEMROOT%\py.exe (
    SET PYTHON_CMD=%SYSTEMROOT%\py.exe -3
) ELSE (
    python --version > NUL 2>&1
    IF %ERRORLEVEL% NEQ 0 (
        ECHO [?] Python не знайдено! Будь ласка, встанов?ть його або додайте в PATH.
        PAUSE
        EXIT /B
    )
    SET PYTHON_CMD=python
)

:: Очищення кешу pip, щоб уникнути помилок ?з неправильними пакетами
ECHO [??] Очищення кешу пакет?в...
%PYTHON_CMD% -m pip cache purge > NUL 2>&1

:: Видалення пошкоджених пакет?в (~t-dlp)
ECHO [??] Видалення пошкоджених пакет?в...
%PYTHON_CMD% -m pip uninstall -y yt-dlp youtube-dl > NUL 2>&1

:: Оновлення pip (без зайвих пов?домлень)
ECHO [??] Оновлення pip...
%PYTHON_CMD% -m pip install --upgrade pip --quiet --break-system-packages > NUL 2>&1

:: Встановлення залежностей, якщо вони в?дсутн?
IF EXIST requirements.txt (
    ECHO [?] Перев?рка та встановлення залежностей...
    %PYTHON_CMD% -m pip install -r requirements.txt --quiet --break-system-packages > NUL 2>&1
) ELSE (
    ECHO [?] Файл requirements.txt не знайдено! Пропускаємо перев?рку залежностей.
)

:: Запуск бота
ECHO [??] Запуск бота...
CMD /k %PYTHON_CMD% run.py

PAUSE
