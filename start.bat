@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ========================================================
echo   🎸 AI Band Transcriber & TAB Generator
echo ========================================================
echo.

:: 1. Check if venv already exists
if exist "venv\Scripts\python.exe" (
    echo [✓] 기존 설치된 AI 환경(venv)을 감지했습니다. 프로그램을 즉시 실행합니다...
    start "" "%~dp0venv\Scripts\python.exe" "%~dp0app_gui.py"
    exit /b 0
)

:: 2. First-time setup notice
echo [!] 가상환경(venv)이 존재하지 않습니다.
echo [*] 최초 1회 자동 설치를 시작합니다. (컴퓨터 사양 및 네트워크에 따라 수 분 소요)
echo.

:: Check python command
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [X] 시스템에 Python이 설치되어 있지 않거나 환경 변수(PATH)에 등록되지 않았습니다!
    echo     Python 3.10 또는 3.11을 설치하시고 'Add Python to PATH'를 꼭 체크해주세요.
    echo.
    pause
    exit /b 1
)

echo [1/3] 가상환경(venv)을 생성하는 중입니다...
python -m venv venv
if %ERRORLEVEL% NEQ 0 (
    echo [X] 가상환경 생성 실패! 파이썬 설치 상태를 확인해주세요.
    pause
    exit /b 1
)
echo [✓] 가상환경 생성 완료!
echo.

echo [2/3] PyTorch (CUDA 12.4 가속 버전) 라이브러리를 설치하는 중입니다...
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0venv\Scripts\python.exe" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
if %ERRORLEVEL% NEQ 0 (
    echo [!] CUDA 버전 설치에 실패하여 일반 CPU 버전으로 재시도합니다...
    "%~dp0venv\Scripts\python.exe" -m pip install torch torchaudio
)
echo [✓] PyTorch 설치 완료!
echo.

echo [3/3] 필수 의존성 라이브러리(requirements.txt)를 일괄 설치하는 중입니다...
"%~dp0venv\Scripts\python.exe" -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [X] 라이브러리 설치 중 오류가 발생했습니다.
    pause
    exit /b 1
)
echo [✓] 모든 필수 라이브러리 설치 완료!
echo.

echo ========================================================
echo   🎉 모든 설치가 완료되었습니다! 프로그램을 실행합니다...
echo ========================================================
echo.

start "" "%~dp0venv\Scripts\python.exe" "%~dp0app_gui.py"
exit /b 0
