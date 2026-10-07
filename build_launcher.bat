@echo off
chcp 65001 > nul
cd /d "%~dp0"

rem Rebuilds AI_Band_Transcriber.exe from Launcher.cs with the C# compiler that ships with
rem Windows (.NET Framework 4.x); no Visual Studio needed.
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" goto :NO_CSC

"%CSC%" /nologo /target:winexe /out:AI_Band_Transcriber.exe /reference:System.Windows.Forms.dll Launcher.cs
if %ERRORLEVEL% NEQ 0 goto :FAILED
echo [OK] AI_Band_Transcriber.exe 빌드 완료.
pause
exit /b 0

:NO_CSC
echo [ERROR] .NET Framework 4.x C# 컴파일러(csc.exe)를 찾을 수 없습니다.
pause
exit /b 1

:FAILED
echo [ERROR] 빌드에 실패했습니다. 위의 오류 메시지를 확인해 주세요.
pause
exit /b 1
