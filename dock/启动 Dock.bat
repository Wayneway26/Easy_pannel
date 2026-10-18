@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 依次寻找带 tkinter 的 Python（Dock 界面需要 tkinter）
set "PYEXE=C:\MiniConda\python.exe"
set "PYW=C:\MiniConda\pythonw.exe"
if exist "%PYEXE%" goto check

set "PYEXE=C:\Windows\py.exe"
set "PYW=C:\Windows\pyw.exe"
if exist "%PYEXE%" goto check

set "PYEXE=C:\Users\27197\.workbuddy\binaries\python\versions\3.13.12\python.exe"
set "PYW=C:\Users\27197\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
if exist "%PYEXE%" goto check

echo 未找到 Python，请安装 Python 3（需包含 tkinter）后重试。
pause
exit /b

:check
"%PYEXE%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo 当前 Python 缺少 tkinter，改用控制台方式启动（会显示黑色窗口）...
  start "Easy Dock" "%PYEXE%" "%~dp0EasyDock.pyw"
  exit /b
)
start "Easy Dock" "%PYW%" "%~dp0EasyDock.pyw"
