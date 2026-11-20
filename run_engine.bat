@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] 아직 설치되지 않았습니다. 먼저 start.bat을 실행해 설치하세요.
    pause
    exit /b 1
)
title AI Band Transcriber 엔진
"%~dp0venv\Scripts\python.exe" "%~dp0engine.py" %*
if %ERRORLEVEL% NEQ 0 pause
