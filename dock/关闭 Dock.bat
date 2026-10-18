@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "dock.pid" (
  echo Dock 当前没有在运行。
  pause
  exit /b
)

set /p DPID=<dock.pid
taskkill /f /pid %DPID% >nul 2>&1
if errorlevel 1 (
  echo 结束进程失败，可能已经退出。请手动在任务管理器里结束 pythonw.exe。
) else (
  echo Dock 已关闭。
)
del "dock.pid" >nul 2>&1
timeout /t 2 >nul
