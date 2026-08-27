@echo off
REM Launcher di MaraMouse: avvia main.py dalla cartella dello script.
REM Eventuali argomenti passati al .bat vengono inoltrati (es. MaraMouse.bat --debug).
cd /d "%~dp0"
python main.py %*
REM Tiene aperta la finestra solo se c'e' stato un errore, per leggere il messaggio.
if errorlevel 1 pause
