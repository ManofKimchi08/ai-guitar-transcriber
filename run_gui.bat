@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ===================================================
echo     AI Band Transcriber GUI 실행 중...
echo ===================================================
start "" "%~dp0venv\Scripts\python.exe" "%~dp0app_gui.py"
exit
