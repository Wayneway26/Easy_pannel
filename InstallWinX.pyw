# -*- coding: utf-8 -*-
"""
把「一键展开托盘图标」安装到 Win+X 菜单（右键开始按钮的菜单）

用法：
    双击本文件                            安装
    pythonw InstallWinX.pyw --remove      卸载（或用「从Win+X移除.bat」）

实现说明：不依赖 pywin32 / PowerShell COM，直接按 MS-SHLLINK 规范生成 .lnk。
目标路径使用环境变量形式（%SystemDrive% / %USERPROFILE%），
这是 HasExpString 数据块被 Shell 正确解析的必要条件（实测：写成绝对路径会导致
"没有应用程序与此操作的指定文件有关联" 1155 错误）。
"""
import os
import struct
import sys
import tkinter as tk
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "assets", "tray-expand.ico")
TARGET = "%SystemDrive%\\MiniConda\\pythonw.exe"
WINX_GROUP = os.path.join(os.environ["LOCALAPPDATA"], r"Microsoft\Windows\WinX\Group3")
LNK_NAME = "90 - 一键展开托盘图标.lnk"
DESC = "一键展开所有托盘图标到任务栏"

HAS_NAME = 0x00000004
HAS_WORKING_DIR = 0x00000010
HAS_ARGUMENTS = 0x00000020
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080
HAS_EXP_STRING = 0x00000200
SIG_ENV_BLOCK = 0xA0000001


def _u16(v):
    return struct.pack("<H", v)


def _u32(v):
    return struct.pack("<I", v)


def _sd(text):
    """StringData：2 字节字符数 + UTF-16LE 字符"""
    return _u16(len(text)) + text.encode("utf-16-le")


def _header(flags):
    h = _u32(0x4C)
    h += bytes([0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
                0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46])
    h += _u32(flags)
    h += _u32(0)          # FileAttributes
    h += b"\x00" * 24     # 三个 FILETIME
    h += _u32(0)          # FileSize
    h += struct.pack("<i", 0)   # IconIndex
    h += _u32(1)          # ShowCommand: SW_SHOWNORMAL
    h += _u16(0) + _u16(0)      # HotKey / Reserved1
    h += _u32(0) + _u32(0)      # Reserved2 / Reserved3
    assert len(h) == 76
    return h


def _env_block(target):
    """EnvironmentVariableDataBlock：固定 788 字节"""
    a = target.encode("mbcs")[:259]
    ansi = a + b"\x00" * (260 - len(a))
    w = target.encode("utf-16-le")[:518]
    uni = w + b"\x00" * (520 - len(w))
    return _u32(788) + _u32(SIG_ENV_BLOCK) + ansi + uni


def create_shortcut(lnk_path, target, arguments="", icon="", description=""):
    flags = IS_UNICODE | HAS_EXP_STRING
    if description:
        flags |= HAS_NAME
    if arguments:
        flags |= HAS_ARGUMENTS
    if icon:
        flags |= HAS_ICON_LOCATION

    data = _header(flags)
    if description:
        data += _sd(description)
    if arguments:
        data += _sd(arguments)
    if icon:
        data += _sd(icon)
    data += _env_block(target)
    data += _u32(0)   # TerminalBlock

    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    with open(lnk_path, "wb") as f:
        f.write(data)
    return lnk_path


def action_arg():
    """把脚本路径转成 %USERPROFILE% 形式，供环境变量数据块展开"""
    profile = os.environ.get("USERPROFILE", "").rstrip("\\")
    script = os.path.join(HERE, "TrayAction.pyw")
    if profile and script.lower().startswith(profile.lower()):
        script = "%USERPROFILE%" + script[len(profile):]
    return '"%s" expand' % script


def install():
    lnk = os.path.join(WINX_GROUP, LNK_NAME)
    create_shortcut(lnk, TARGET, action_arg(), ICON, DESC)
    return lnk


def remove():
    lnk = os.path.join(WINX_GROUP, LNK_NAME)
    if os.path.exists(lnk):
        os.remove(lnk)
        return True
    return False


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    if "--remove" in sys.argv:
        ok = remove()
        messagebox.showinfo("托盘图标助手",
                            "已从 Win+X 菜单移除。" if ok else "菜单项不存在，无需移除。")
        return

    try:
        lnk = install()
    except Exception as e:
        messagebox.showerror("托盘图标助手", "安装失败：%s" % e)
        return
    messagebox.showinfo(
        "托盘图标助手",
        "已添加到 Win+X 菜单。\n\n使用方法：右键点击「开始」按钮 → 「一键展开托盘图标」。\n"
        "若菜单里没有立即出现，重启一次 Explorer 即可（任务栏会闪一下）。")


if __name__ == "__main__":
    main()
