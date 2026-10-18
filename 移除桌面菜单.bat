@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "C:\MiniConda\pythonw.exe" "%~dp0InstallContextMenu.pyw" --remove
