@echo off
chcp 65001 >nul
title 托盘图标一键展开
echo ============================================
echo   顺序：先结束 Explorer -^> 写注册表 -^> 再启动
echo   （顺序不能反，否则修改会被 Explorer 覆盖）
echo ============================================
set /p ok=确认执行？(Y/N)：
if /i not "%ok%"=="Y" exit /b

echo [1/3] 正在结束 Explorer ...
taskkill /f /im explorer.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/3] 正在写入注册表 ...
for /f "delims=" %%k in ('reg query "HKCU\Control Panel\NotifyIconSettings" 2^>nul ^| findstr /r "^ *[0-9][0-9]*$"') do (
  reg add "HKCU\Control Panel\NotifyIconSettings\%%k" /v IsPromoted /t REG_DWORD /d 1 /f >nul 2>&1
)

echo [3/3] 正在启动 Explorer ...
start "" "%SystemRoot%\explorer.exe"
echo 完成，请查看任务栏右下角。
timeout /t 3 >nul
