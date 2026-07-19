@echo off
chcp 65001 >nul
title ERAG Control

cd /d "%~dp0..\.."

echo.
echo   ===================================
echo     EnterpriseRAG-Lite
echo   ===================================
echo.
echo     [1] Start All
echo     [2] Reload All
echo     [3] Stop All
echo     [4] Status
echo     [5] Backend Only
echo     [0] Exit
echo.
set /p choice="Choose: "

if "%choice%"=="1" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" start
if "%choice%"=="2" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" reload
if "%choice%"=="3" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" stop
if "%choice%"=="4" powershell -ExecutionPolicy Bypass -File "bin\psl\start.ps1" status
if "%choice%"=="5" powershell -ExecutionPolicy Bypass -File "bin\psl\backend.ps1" start

pause
