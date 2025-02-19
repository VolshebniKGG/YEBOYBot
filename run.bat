@ECHO off
CHCP 866 > NUL  & REM ��⠭������� ���㢠��� CP866 ��� ��४⭮�� �?���ࠦ���� ��ਫ��?

:: ����?� � ����� �? �ਯ⮬
CD /d "%~dp0"

:: ��ॢ?���, � ��客��? ஧�७�� 䠩�?�
SET KEY_NAME="HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
SET VALUE_NAME=HideFileExt

FOR /F "usebackq tokens=1-3" %%A IN (`REG QUERY %KEY_NAME% /v %VALUE_NAME% 2^>nul`) DO (
    SET ValueValue=%%C
)

IF x%ValueValue:0x0=%==x%ValueValue% (
    ECHO �?���ࠦ���� ஧�७� 䠩�?�...
    REG ADD HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced /v HideFileExt /t REG_DWORD /d 0 /f > NUL
)

:: ��ॢ?ઠ �����? Python
IF EXIST %SYSTEMROOT%\py.exe (
    SET PYTHON_CMD=%SYSTEMROOT%\py.exe -3
) ELSE (
    python --version > NUL 2>&1
    IF %ERRORLEVEL% NEQ 0 (
        ECHO [?] Python �� ��������! ��� ��᪠, ��⠭��?�� ���� ��� ������ � PATH.
        PAUSE
        EXIT /B
    )
    SET PYTHON_CMD=python
)

:: ��饭�� ���� pip, 鮡 㭨���� ������� ?� ���ࠢ��쭨�� ����⠬�
ECHO [??] ��饭�� ���� �����?�...
%PYTHON_CMD% -m pip cache purge > NUL 2>&1

:: ��������� ��誮������ �����?� (~t-dlp)
ECHO [??] ��������� ��誮������ �����?�...
%PYTHON_CMD% -m pip uninstall -y yt-dlp youtube-dl > NUL 2>&1

:: ��������� pip (��� ������ ���?�������)
ECHO [??] ��������� pip...
%PYTHON_CMD% -m pip install --upgrade pip --quiet --break-system-packages > NUL 2>&1

:: ��⠭������� ��������⥩, �� ���� �?����?
IF EXIST requirements.txt (
    ECHO [?] ��ॢ?ઠ � ��⠭������� ��������⥩...
    %PYTHON_CMD% -m pip install -r requirements.txt --quiet --break-system-packages > NUL 2>&1
) ELSE (
    ECHO [?] ���� requirements.txt �� ��������! �ய�᪠� ��ॢ?�� ��������⥩.
)

:: ����� ���
ECHO [??] ����� ���...
CMD /k %PYTHON_CMD% run.py

PAUSE
