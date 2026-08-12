@echo off
title QQ 구조 진단
cd /d "%~dp0"

echo.
echo  ============================================================
echo    QQ 채널 구조 진단
echo  ============================================================
echo.
echo   실제 QQ 페이지를 열어 구조를 기록합니다.
echo   아무것도 수정하지 않고 읽기만 합니다.
echo.
echo   1~2분 걸립니다. 창을 닫지 마세요.
echo.
pause

set "PYEXE="
if exist "python\python.exe" set "PYEXE=python\python.exe"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"

if not defined PYEXE (
  echo   파이썬을 찾지 못했습니다.
  pause
  exit /b 1
)

"%PYEXE%" diagnose_qq.py

echo.
echo  ============================================================
echo   qq_diagnose.txt 파일이 만들어졌습니다.
echo  ============================================================
echo.
pause
