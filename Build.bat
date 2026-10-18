@echo off
cd /d "%~dp0"
echo ============================================
echo   TrayAllShow - Building TrayAllShow.exe ...
echo ============================================

set CSC64=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
set CSC32=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe

if exist "%CSC64%" (
  "%CSC64%" /nologo /codepage:65001 /target:winexe /platform:anycpu /out:"%~dp0TrayAllShow.exe" /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll "%~dp0TrayAllShow.cs"
) else if exist "%CSC32%" (
  "%CSC32%" /nologo /codepage:65001 /target:winexe /platform:anycpu /out:"%~dp0TrayAllShow.exe" /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll "%~dp0TrayAllShow.cs"
) else (
  echo [ERROR] .NET Framework compiler not found.
  pause
  exit /b 1
)

if errorlevel 1 (
  echo.
  echo [FAILED] Compile failed, see errors above.
) else (
  echo.
  echo [OK] Build success: "%~dp0TrayAllShow.exe"
  echo You can double-click TrayAllShow.exe now, or move it anywhere you like.
)
pause
