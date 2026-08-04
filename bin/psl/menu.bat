@echo off
chcp 65001 >nul
title RAGClaw Control

cd /d "%~dp0..\.."

echo.
echo   ===================================
echo     RAGClaw-Lite
echo   ===================================
echo.
echo     [1] Start All
echo "        -> compose build (ragclaw mcp-repl ragclaw-egress nginx) + compose up -d"
echo     [2] Reload All
echo "        -> compose up -d --force-recreate   (containers only, NO image rebuild)"
echo     [3] Stop All
echo "        -> compose stop   (pause all containers; images & volumes kept)"
echo     [4] Status
echo "        -> compose ps   (services / published ports / health)"
echo     [5] Backend Only
echo "        -> compose build ragclaw + compose up -d ragclaw   (backend service only)"
echo     [0] Exit
echo.
set /p choice="Choose: "

if "%choice%"=="1" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" start
if "%choice%"=="2" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" reload
if "%choice%"=="3" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" stop
if "%choice%"=="4" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" status
if "%choice%"=="5" powershell -ExecutionPolicy Bypass -File "bin\psl\backend.ps1" start

pause
