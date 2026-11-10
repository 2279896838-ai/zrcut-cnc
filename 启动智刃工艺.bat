@echo off
chcp 65001 >nul
setlocal
title 智刃工艺 ZR-CUT - CNC 切削工艺参数智能推荐系统

echo ============================================================
echo   智刃工艺 ZR-CUT
echo   CNC 切削工艺参数智能推荐系统
echo ============================================================
echo.

cd /d "%~dp0"

rem ---------- 定位 Python ----------
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
    echo 请安装 Python 3.10 及以上版本，并确保 python 在 PATH 中。
    pause
    exit /b 1
)

echo 使用解释器: %PY%
"%PY%" --version
echo.

rem ---------- 检查 numpy ----------
"%PY%" -c "import numpy" >nul 2>nul
if errorlevel 1 (
    echo [错误] 缺少依赖 numpy。请执行: "%PY%" -m pip install numpy
    pause
    exit /b 1
)

rem ---------- 自检 ----------
echo 正在执行启动自检...
"%PY%" -c "from zrcut.service import ZrCutService; s=ZrCutService(); r=s.recommend(material='M1', tool='T3', operation='OP2', ra_req=1.6); print('  自检通过 - 推荐 n=%.0f r/min, Pc=%.2f kW' % (r['primary']['n'], r['primary']['pc']))"
if errorlevel 1 (
    echo [错误] 启动自检失败，请检查安装。
    pause
    exit /b 1
)
echo.

rem ---------- 启动服务 ----------
set "HOST=127.0.0.1"
set "PORT=8077"
echo 正在启动 HTTP API: http://%HOST%:%PORT%
echo   健康检查: http://%HOST%:%PORT%/health
echo   接口元数据: http://%HOST%:%PORT%/api/v1/meta
echo.
echo 按 Ctrl+C 停止服务。
echo ============================================================
echo.

"%PY%" -m zrcut.cli serve --host %HOST% --port %PORT%

if errorlevel 1 (
    echo.
    echo [提示] 服务异常退出。若端口被占用，请修改本脚本中的 PORT 变量。
    pause
)

endlocal
