# -*- coding: utf-8 -*-
"""
安装「一键展开托盘图标」到：
  1. 桌面右键菜单（立即生效，不需要重启或注销）
  2. 桌面快捷方式（可右键 → 固定到任务栏）

用法：
    双击本文件                           安装
    pythonw InstallContextMenu.pyw --remove   卸载
"""
import ctypes
import importlib.util
import os
import sys
import tkinter as tk
import winreg
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ACTION = os.path.join(HERE, "TrayAction.pyw")
ICON = os.path.join(HERE, "assets", "tray-expand.ico")
PYTHONW = r"C:\MiniConda\pythonw.exe"
SHELL_KEY = r"Software\Classes\DesktopBackground\shell"

# 菜单项：shlex 风格命令行（Explorer 会展开 %环境变量%）
ITEMS = [
    ("expand", "一键展开托盘图标", "展开所有托盘图标到任务栏"),
    ("collapse", "一键收起托盘图标", "把所有托盘图标收进倒三角"),
]


def desktop_dir():
    """取真实桌面路径（兼容 OneDrive 重定向）"""
    buf = ctypes.create_unicode_buffer(260)
    # CSIDL_DESKTOPDIRECTORY = 0x0010
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
    return buf.value


def pythonw_path():
    """优先使用当前解释器同目录的 pythonw，找不到再退回默认路径"""
    exe = sys.executable or ""
    low = exe.lower()
    if low.endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    if low.endswith("pythonw.exe") and os.path.exists(exe):
        return exe
    return PYTHONW


def env_form(path):
    """把绝对路径转成环境变量形式；不在系统盘/用户目录下时保持原样。

    .lnk 的目标只有写成 %SystemDrive%\\... 这类形式才能被 Shell 正确解析，
    但**必须先确认路径真在该前缀之下**，否则会切出错误路径。
    """
    drive = os.environ.get("SystemDrive", "C:")
    if path.lower().startswith(drive.lower() + "\\"):
        return "%SystemDrive%" + path[len(drive):]
    profile = os.environ.get("USERPROFILE", "").rstrip("\\")
    if profile and path.lower().startswith(profile.lower() + "\\"):
        return "%USERPROFILE%" + path[len(profile):]
    return path


def action_cmd(action):
    return '"%s" "%s" %s' % (env_form(pythonw_path()), env_form(ACTION), action)


def install():
    made = []
    for action, label, _desc in ITEMS:
        base = SHELL_KEY + r"\TrayIconHelper." + action
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, ICON)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, action_cmd(action))
        made.append(label)

    # 桌面快捷方式（方便固定到任务栏）
    spec = importlib.util.spec_from_file_location("winx", os.path.join(HERE, "InstallWinX.pyw"))
    winx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(winx)
    lnk = os.path.join(desktop_dir(), "一键展开托盘图标.lnk")
    winx.create_shortcut(lnk, env_form(pythonw_path()),
                         '"%s" expand' % env_form(ACTION),
                         ICON, "一键展开所有托盘图标到任务栏")
    made.append("桌面快捷方式")
    return made


def remove():
    n = 0
    for action, _label, _desc in ITEMS:
        base = SHELL_KEY + r"\TrayIconHelper." + action
        for path in (base + r"\command", base):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
                n += 1
            except FileNotFoundError:
                pass
    lnk = os.path.join(desktop_dir(), "一键展开托盘图标.lnk")
    if os.path.exists(lnk):
        os.remove(lnk)
        n += 1
    return n


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if "--remove" in sys.argv:
        n = remove()
        messagebox.showinfo("托盘图标助手",
                            "已移除 %d 项（桌面右键菜单 + 桌面快捷方式）。" % n if n else "没有找到已安装的项。")
        return
    try:
        made = install()
    except Exception as e:
        messagebox.showerror("托盘图标助手", "安装失败：%s" % e)
        return
    messagebox.showinfo(
        "托盘图标助手",
        "已安装：%s\n\n"
        "① 桌面右键 → 「显示更多选项」→ 一键展开托盘图标\n"
        "② 桌面上也有快捷方式，可右键 →「固定到任务栏」，之后单击即可" % "、".join(made))


if __name__ == "__main__":
    main()
