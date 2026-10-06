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

rem 필수 패키지는 Python 3.10 / 3.11용만 제공되므로, py 런처로 해당 버전을 먼저 찾습니다.
set "PY="
py -3.11 -c "import sys" >nul 2>nul
if %ERRORLEVEL% EQU 0 set "PY=py -3.11"
if not defined PY py -3.10 -c "import sys" >nul 2>nul
if not defined PY if %ERRORLEVEL% EQU 0 set "PY=py -3.10"
if defined PY goto :PY_READY

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 goto :NO_PYTHON
set "PY=python"

:PY_READY
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] in ((3, 10), (3, 11)) else 1)"
if %ERRORLEVEL% NEQ 0 goto :BAD_PYTHON
echo [*] 사용할 Python: %PY%
echo.

echo [1/3] Python venv 가상환경 생성 중...
%PY% -m venv venv
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

:NO_PYTHON
echo [ERROR] Python이 설치되어 있지 않거나 PATH 환경변수에 등록되지 않았습니다.
echo         Python 3.10 또는 3.11을 설치하시고 Add Python to PATH를 체크해주세요.
echo.
pause
exit /b 1

:BAD_PYTHON
echo [ERROR] Python 3.10 또는 3.11이 필요합니다. 현재 Python:
%PY% --version
echo         일부 필수 패키지가 다른 버전용으로는 제공되지 않습니다.
echo         python.org에서 Python 3.11을 설치하시고 Add Python to PATH를 체크해주세요.
echo.
pause
exit /b 1