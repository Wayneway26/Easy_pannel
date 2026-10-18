@echo off
chcp 65001 >nul
title 恢复桌面 / 任务栏
color 0F

echo.
echo   ============================================
echo      桌面 / 任务栏 紧急恢复
echo   ============================================
echo.
echo   桌面变黑、任务栏消失，通常只是 explorer.exe 没在运行。
echo   本工具会自动检测并重新启动它，不会改动任何设置。
echo.

set "N=0"

:check
tasklist /fi "imagename eq explorer.exe" 2>nul | find /i "explorer.exe" >nul
if not errorlevel 1 goto ok

set /a N+=1
if %N% GTR 6 goto fail

echo   [%N/6] 未检测到 Explorer，正在启动 ...
start "" "%SystemRoot%\explorer.exe"
timeout /t 2 /nobreak >nul
goto check

:ok
echo.
echo   [成功] Explorer 已在运行。
echo          桌面图标和任务栏应该已经恢复（可能需要等 1-2 秒）。
echo.
echo   如果画面仍然是黑的，按 Ctrl+Shift+Esc 打开任务管理器，
echo   然后：文件 - 运行新任务 - 输入 explorer.exe - 回车。
echo.
pause
exit /b 0

:fail
echo.
echo   [失败] 多次尝试后 Explorer 仍未启动。
echo.
echo   请按下面任一方式手动恢复：
echo     1) Ctrl+Shift+Esc 打开任务管理器 - 文件 - 运行新任务 - 输入 explorer.exe
echo     2) Ctrl+Alt+Del - 注销 - 重新登录
echo     3) 重启电脑
echo.
echo   说明：本工具只做了“启动 explorer.exe”这一件事，
echo         没有修改注册表，不会造成其他影响。
echo.
pause
exit /b 1
