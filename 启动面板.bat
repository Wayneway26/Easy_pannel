@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 依次寻找带 tkinter 的 pythonw.exe（GUI 必须有 tkinter）
set "PYW="
for %%P in (
  "C:\MiniConda\pythonw.exe"
  "C:\Windows\pyw.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
) do if not defined PYW if exist "%%~P" set "PYW=%%~P"

if not defined PYW for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"

if not defined PYW (
  echo 未找到 Python 3 的 pythonw.exe，请安装 Python 3（需包含 tkinter）后重试。
  pause
  exit /b
)

start "" "%PYW%" "%~dp0EasyPanel.pyw"
