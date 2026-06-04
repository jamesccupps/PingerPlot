@echo off
setlocal
title PingerPlot Setup
set "PS=powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1""

:menu
cls
echo ===================================
echo    PingerPlot - Setup
echo ===================================
echo.
echo   1.  Launch now
echo   2.  Launch elevated (for TCP/UDP traceroute)
echo   3.  Create Desktop / Start-menu shortcuts
echo   4.  Enable auto-start at logon (elevated, no UAC nag)
echo   5.  Disable auto-start
echo   6.  Status
echo   0.  Exit
echo.
set "choice="
set /p "choice=Choose: "

if "%choice%"=="1" %PS%
if "%choice%"=="2" %PS% -Elevated
if "%choice%"=="3" %PS% -CreateShortcuts
if "%choice%"=="4" %PS% -InstallAutostart
if "%choice%"=="5" %PS% -UninstallAutostart
if "%choice%"=="6" %PS% -Status
if "%choice%"=="0" goto :eof
goto menu
