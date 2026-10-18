@echo off
chcp 65001 >nul
title 重启资源管理器
echo 正在重启 Explorer，刷新 Win+X 菜单...
taskkill /f /im explorer.exe >nul 2>&1
timeout /t 2 /nobreak >nul
start "" "%SystemRoot%\explorer.exe"
echo 完成。现在右键点击「开始」按钮，检查菜单中是否出现「一键展开托盘图标」。
timeout /t 3 >nul
