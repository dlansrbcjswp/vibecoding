@echo off
title 리니지2M 중국 유저 동향 툴
cd /d "%~dp0"

rem  Streamlit 최초 실행 시 이메일 입력 화면을 건너뜁니다.
rem  이 설정은 프로젝트 폴더가 아니라 사용자 폴더에 있어야 적용됩니다.
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit" 2>nul
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  >"%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >>"%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"

set "PYEXE="
if exist "python\python.exe" set "PYEXE=python\python.exe"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"

if not defined PYEXE goto NOPYTHON

rem  이미 사용 중인 포트를 피해 비어 있는 포트를 찾습니다.
set PORT=8501
:CHECKPORT
netstat -an | findstr /c:":%PORT% " | findstr /i "LISTENING" >nul 2>&1
if errorlevel 1 goto PORTOK
set /a PORT+=1
if %PORT% LSS 8511 goto CHECKPORT

:PORTOK
echo.
echo  ================================================
echo   리니지2M 중국 유저 동향 툴
echo  ================================================
echo.
echo   주소 : http://localhost:%PORT%
echo   브라우저가 자동으로 열립니다.
echo   안 열리면 위 주소를 직접 입력하세요.
echo.
echo   이 창을 닫으면 툴이 종료됩니다.
echo   종료하려면 Ctrl + C 를 누르세요.
echo.
echo   Email 입력 화면이 나오면 Enter 만 누르세요.
echo.

"%PYEXE%" -m streamlit run app.py --server.port=%PORT% --server.headless=false

echo.
echo  툴이 종료되었습니다.
pause
exit /b 0

:NOPYTHON
echo.
echo  ============================================================
echo    실행할 파이썬을 찾지 못했습니다
echo  ============================================================
echo.
echo   [ 배포판을 전달받은 경우 ]
echo     압축을 폴더째 풀지 않았을 가능성이 높습니다.
echo     압축 파일 안에서 바로 실행하면 안 됩니다.
echo.
echo   [ 배포판을 만들려는 경우 ]
echo     BUILD_portable.bat 을 먼저 실행하세요.
echo.
pause
exit /b 1
