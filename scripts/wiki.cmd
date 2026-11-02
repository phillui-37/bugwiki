@echo off
rem wiki.cmd - Windows shim so `wiki find "..."` works from cmd and PowerShell.
setlocal enabledelayedexpansion
set "SCRIPT=%~dp0wiki.ps1"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)
exit /b %ERRORLEVEL%
