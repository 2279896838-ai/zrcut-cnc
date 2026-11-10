@echo off
chcp 65001 >nul
setlocal
title 智刃工艺 - 运行全部测试

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
echo   智刃工艺 - 测试套件
echo ============================================================
echo 解释器: %PY%
echo.

echo [1/3] 运行单元测试...
echo ------------------------------------------------------------
"%PY%" -m unittest discover -s tests -t . -v
if errorlevel 1 (
    echo.
    echo [失败] 单元测试未通过。
    pause
    exit /b 1
)
echo.

echo [2/3] 测算代码覆盖率...
echo ------------------------------------------------------------
"%PY%" scripts\coverage.py --threshold 85
if errorlevel 1 (
    echo.
    echo [失败] 覆盖率未达标。
    pause
    exit /b 1
)
echo.

echo [3/3] 运行 400 条评估集...
echo ------------------------------------------------------------
"%PY%" -m zrcut.cli eval
if errorlevel 1 (
    echo.
    echo [失败] 评估集未达标。
    pause
    exit /b 1
)
echo.

echo ============================================================
echo   全部通过
echo ============================================================
pause
endlocal
