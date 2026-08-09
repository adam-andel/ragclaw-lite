@echo off
chcp 65001 >nul
title RAGClaw 控制菜单

cd /d "%~dp0..\.."

REM ===== 三个镜像源（全部传入底层脚本；留空即官方源）=====
REM SOURCE_REGISTRY : Docker 基础镜像仓库域名，空 -> docker.io
REM SOURCE_APT      : Debian apt 镜像主机名（可带也可不带 https://），空 -> 发行版官方源
REM SOURCE_PYPI     : PyPI 镜像地址（需完整 URL，如 https://pypi.tuna.tsinghua.edu.cn/simple），空 -> 官方 pypi.org
set "SOURCE_REGISTRY=docker.1ms.run"
set "SOURCE_APT=mirrors.tuna.tsinghua.edu.cn"
set "SOURCE_PYPI=https://pypi.tuna.tsinghua.edu.cn/simple"

echo.
echo   ===================================
echo     RAGClaw-Lite 控制菜单
echo   ===================================
echo.
echo     当前镜像源设置：
echo       registry : %SOURCE_REGISTRY%
echo       apt      : %SOURCE_APT%
echo       pypi     : %SOURCE_PYPI%
echo.
echo     [1] 启动全部（生产环境）
echo         -^> 构建镜像（ragclaw mcp-repl ragclaw-egress nginx）+ 启动容器
echo     [2] 重新加载全部（生产环境）
echo         -^> 仅重建容器（compose up -d --force-recreate），不重新构建镜像
echo     [3] 停止全部
echo         -^> 暂停所有容器（镜像与数据卷保留）
echo     [4] 查看状态
echo         -^> 列出服务 / 对外端口 / 健康状态
echo     [5] 仅后端（生产环境）
echo         -^> 仅构建并启动后端服务（ragclaw）
echo     [0] 退出
echo.
set /p choice="请选择: "

if "%choice%"=="1" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" -Action start -Registry "%SOURCE_REGISTRY%" -Apt "%SOURCE_APT%" -Pypi "%SOURCE_PYPI%"
if "%choice%"=="2" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" -Action reload -Registry "%SOURCE_REGISTRY%" -Apt "%SOURCE_APT%" -Pypi "%SOURCE_PYPI%"
if "%choice%"=="3" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" -Action stop
if "%choice%"=="4" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" -Action status
if "%choice%"=="5" powershell -ExecutionPolicy Bypass -File "bin\psl\backend.ps1" -Action start -Registry "%SOURCE_REGISTRY%" -Apt "%SOURCE_APT%" -Pypi "%SOURCE_PYPI%"

pause
