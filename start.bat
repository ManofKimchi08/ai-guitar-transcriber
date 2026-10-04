@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ========================================================
echo   AI Band Transcriber - Setup ^& Launch
echo ========================================================
echo.

if exist "venv\Scripts\python.exe" if exist "venv\.installed" goto :LAUNCH

goto :INSTALL

:LAUNCH
echo [*] 기존 설치된 AI 환경을 감지했습니다. 프로그램을 실행합니다...
start "" "%~dp0venv\Scripts\python.exe" "%~dp0app_gui.py"
exit /b 0

:INSTALL
echo [*] 가상환경이 없거나 설치가 완료되지 않았습니다.
echo [*] 최초 1회 자동 설치를 시작합니다.
echo.

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python이 설치되어 있지 않거나 PATH 환경변수에 등록되지 않았습니다.
    echo         Python 3.10 또는 3.11을 설치하시고 Add Python to PATH를 체크해주세요.
    echo.
    pause
    exit /b 1
)

echo [1/3] Python venv 가상환경 생성 중...
python -m venv venv
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 가상환경 생성에 실패했습니다. Python 설치 상태를 확인해주세요.
    pause
    exit /b 1
)
echo [OK] 가상환경 생성 완료.
echo.

echo [2/3] PyTorch CUDA 12.4 가속 버전 설치 중...
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0venv\Scripts\python.exe" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] CUDA 버전 설치 실패. CPU 전용 버전으로 재시도합니다...
    "%~dp0venv\Scripts\python.exe" -m pip install torch torchaudio
)
echo [OK] PyTorch 설치 완료.
echo.

echo [3/3] 필수 패키지 목록(requirements.txt) 설치 중...
"%~dp0venv\Scripts\python.exe" -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 패키지 설치 중 오류가 발생했습니다.
    pause
    exit /b 1
)

if not exist "%~dp0venv" mkdir "%~dp0venv" 2>nul
echo installed > "%~dp0venv\.installed"
echo [OK] 모든 라이브러리 설치 완료.
echo.
echo ========================================================
echo   설치가 완료되었습니다. 프로그램을 실행합니다...
echo ========================================================
echo.

start "" "%~dp0venv\Scripts\python.exe" "%~dp0app_gui.py"
exit /b 0