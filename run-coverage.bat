@echo off
chcp 65001 >nul
setlocal
title 智刃工艺 - 覆盖率测算

cd /d "%~dp0"

set "PY="
if exist "C:\Users\22798\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" (
    set "PY=C:\Users\22798\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [错误] 未找到 Python 解释器。
    pause
    exit /b 1
)

echo ============================================================
echo   智刃工艺 - 代码覆盖率
echo ============================================================
echo.

if "%~1"=="--verbose" (
    "%PY%" scripts\coverage.py --threshold 85 --verbose
) else (
    "%PY%" scripts\coverage.py --threshold 85
)

set "RC=%ERRORLEVEL%"
echo.
if %RC%==0 (
    echo 覆盖率达标。
) else (
    echo 覆盖率未达标，请补充测试。
)
pause
exit /b %RC%
