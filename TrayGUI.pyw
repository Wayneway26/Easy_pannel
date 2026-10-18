# -*- coding: utf-8 -*-
"""
托盘图标助手 GUI
- 一键全部展开：把所有托盘图标显示到任务栏
- 一键恢复收纳：把所有托盘图标收进右下角倒三角
关键点：必须先结束 Explorer，再写注册表，最后再启动 Explorer。
（Explorer 退出时会把内存中的托盘状态回写注册表，顺序颠倒会导致修改被覆盖）
"""
import os
import ctypes
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
import winreg

NOTIFY = r"Control Panel\NotifyIconSettings"
SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
EXPLORER = os.path.join(SYSROOT, "explorer.exe")


def enum_subkeys(writable=False):
    """枚举所有托盘图标子键，返回 key 对象列表"""
    keys = []
    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if writable else 0)
    root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY, 0, access)
    i = 0
    while True:
        try:
            name = winreg.EnumKey(root, i)
        except OSError:
            break
        try:
            sub = winreg.OpenKey(root, name, 0, access)
            keys.append((name, sub))
        except OSError:
            pass
        i += 1
    return root, keys


def get_counts():
    """返回 (显示数, 收纳数, 缺失值数)"""
    shown = hidden = missing = 0
    try:
        root, keys = enum_subkeys(False)
    except FileNotFoundError:
        return (0, 0, 0)
    for name, sub in keys:
        try:
            v, _ = winreg.QueryValueEx(sub, "IsPromoted")
            if int(v) == 1:
                shown += 1
            else:
                hidden += 1
        except OSError:
            missing += 1
        finally:
            winreg.CloseKey(sub)
    winreg.CloseKey(root)
    return (shown, hidden, missing)


def set_all_promoted(value):
    """把所有托盘图标的 IsPromoted 设为 value(1 显示 / 0 收纳)，返回写入数量"""
    n = 0
    errs = []
    root, keys = enum_subkeys(True)
    for name, sub in keys:
        try:
            winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, int(value))
            n += 1
        except OSError as e:
            errs.append("%s: %s" % (name, e))
        finally:
            winreg.CloseKey(sub)
    winreg.CloseKey(root)
    return n, errs


def explorer_running():
    """判断 Explorer 外壳是否存活。tasklist 失败时回退到窗口探测，避免误判为 True。"""
    try:
        # 中文系统 tasklist 输出为 GBK，直接按字节判断避免解码错误
        out = subprocess.run(["tasklist", "/fi", "imagename eq explorer.exe"],
                             capture_output=True, timeout=10).stdout
        return b"explorer.exe" in out.lower()
    except Exception:
        try:
            return bool(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None))
        except Exception:
            return False


def kill_explorer():
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                   capture_output=True, timeout=15)
    for _ in range(20):          # 最多等 5 秒确认进程已退出
        if not explorer_running():
            return True
        time.sleep(0.25)
    return not explorer_running()


# ---- 关键：启动 Explorer 必须"完全脱离"当前进程，否则父进程退出会把它一起带走 ----
# 这三条是桌面变黑（explorer 未拉起）的根因修复：
#   DETACHED_PROCESS        不继承控制台，进程独立存活
#   CREATE_NEW_PROCESS_GROUP 独立进程组，不随父进程信号终止
#   CREATE_BREAKAWAY_FROM_JOB 从父进程的 Job 对象中脱离（Job 关闭时不会被杀）
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_BASE_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _spawn_detached(exe, args=None, flags=_BASE_FLAGS):
    """以尽可能独立的方式启动进程；失败时逐级降级重试。"""
    cmd = [exe] + (args or [])
    attempts = [flags | CREATE_BREAKAWAY_FROM_JOB, flags, 0]
    last = None
    for fl in attempts:
        try:
            subprocess.Popen(cmd, close_fds=True, creationflags=fl,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        except OSError as e:
            last = e
    if last:
        raise last
    return False


def start_explorer(wait=6.0):
    """启动 Explorer 并确认真的起来了（黑屏事故的修复点）。"""
    if explorer_running():
        return True
    for _ in range(3):
        try:
            _spawn_detached(EXPLORER)
        except Exception:
            try:                      # 最后一招：交给 cmd 的 start 兜底
                _spawn_detached(os.path.join(SYSROOT, "System32", "cmd.exe"),
                                ["/c", "start", "", EXPLORER])
            except Exception:
                pass
        deadline = time.time() + wait / 3.0
        while time.time() < deadline:
            time.sleep(0.25)
            if explorer_running():
                return True
    return explorer_running()


TRAY_CACHE = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\TrayNotify"


def spawn_watchdog(grace=10):
    """动 Explorer 之前先放出看门狗；若本程序中途崩溃导致桌面黑屏，由它兜底恢复。"""
    wd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ExplorerWatchdog.pyw")
    if not os.path.exists(wd):
        return False
    try:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            cand = exe[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(cand):
                exe = cand
        _spawn_detached(exe, [wd, str(grace)])
        return True
    except Exception:
        return False


def reset_tray_cache():
    """删除托盘图标缓存（IconStreams / PastIconsStream），用于排查图标状态异常"""
    removed = []
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, TRAY_CACHE, 0, winreg.KEY_SET_VALUE)
        for name in ("IconStreams", "PastIconsStream"):
            try:
                winreg.DeleteValue(key, name)
                removed.append(name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass
    return removed


class App(object):
    def __init__(self, root):
        self.root = root
        root.title("托盘图标助手")
        root.geometry("560x470")
        root.resizable(False, False)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        self.info = tk.Label(root, text="", justify="left", anchor="w",
                             font=("Microsoft YaHei UI", 10), fg="#333")
        self.info.pack(fill="x", padx=14, pady=(12, 8))

        btns = tk.Frame(root)
        btns.pack(fill="x", padx=14)

        self.btn_expand = tk.Button(btns, text="一键全部展开", bg="#e60651", fg="white",
                                    activebackground="#c90546", activeforeground="white",
                                    relief="flat", height=2, cursor="hand2",
                                    font=("Microsoft YaHei UI", 11, "bold"),
                                    command=lambda: self.do_action(1))
        self.btn_expand.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_collapse = tk.Button(btns, text="一键恢复收纳", bg="#4a4a4a", fg="white",
                                      activebackground="#333", activeforeground="white",
                                      relief="flat", height=2, cursor="hand2",
                                      font=("Microsoft YaHei UI", 11, "bold"),
                                      command=lambda: self.do_action(0))
        self.btn_collapse.pack(side="left", expand=True, fill="x", padx=(6, 0))

        self.var_restart = tk.BooleanVar(value=True)
        chk = tk.Checkbutton(root, text="操作后重启 Explorer 使设置生效（先结束→写注册表→再启动）",
                             variable=self.var_restart, anchor="w",
                             font=("Microsoft YaHei UI", 9))
        chk.pack(fill="x", padx=14, pady=(10, 4))

        tip = tk.Label(root, text="说明：展开后新装软件的图标若进入倒三角，再点一次即可全部展开。",
                       fg="#888", anchor="w", font=("Microsoft YaHei UI", 8))
        tip.pack(fill="x", padx=14)

        ops = tk.Frame(root)
        ops.pack(fill="x", padx=14, pady=(8, 4))
        tk.Button(ops, text="刷新状态", width=10, cursor="hand2",
                  command=self.refresh).pack(side="left")
        tk.Button(ops, text="打开系统托盘设置", width=16, cursor="hand2",
                  command=self.open_settings).pack(side="left", padx=6)
        tk.Button(ops, text="仅重启 Explorer", width=14, cursor="hand2",
                  command=self.restart_only).pack(side="left")
        tk.Button(ops, text="重置托盘图标缓存", width=16, cursor="hand2",
                  command=self.reset_cache).pack(side="left", padx=6)

        self.log = scrolledtext.ScrolledText(root, height=13, font=("Consolas", 9),
                                             bg="white", relief="solid", bd=1)
        self.log.pack(fill="both", expand=True, padx=14, pady=(6, 12))

        self.refresh()
        self.write_log("就绪。修改仅影响当前用户，且可随时反向操作。")

    def write_log(self, msg):
        self.log.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        self.log.see("end")

    def refresh(self):
        shown, hidden, missing = get_counts()
        total = shown + hidden + missing
        self.info.config(text="托盘图标记录 %d 个：%d 个显示在任务栏，%d 个收在溢出区，%d 个暂无设置值"
                              % (total, shown, hidden, missing))
        return shown, hidden, missing

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.btn_expand.config(state=state)
        self.btn_collapse.config(state=state)

    def do_action(self, value):
        word = "展开到任务栏" if value == 1 else "收进倒三角"
        if not messagebox.askyesno("确认", "将把所有托盘图标%s，确定吗？" % word):
            return
        if self.var_restart.get():
            if not messagebox.askyesno("确认",
                                       "需要重启 Explorer：任务栏会短暂消失后自动恢复，\n"
                                       "已打开的窗口不受影响。继续吗？"):
                return
        self.set_busy(True)
        threading.Thread(target=self._worker, args=(value,), daemon=True).start()

    def _worker(self, value):
        word = "展开" if value == 1 else "收纳"
        try:
            # 1) 先结束 Explorer，避免它退出时把内存状态回写覆盖注册表
            if self.var_restart.get():
                spawn_watchdog(10)      # 兜底：本程序异常退出也能把桌面救回来
                self._log("正在结束 Explorer ...")
                ok = kill_explorer()
                if not ok:
                    self._log("警告：Explorer 未能完全结束，修改可能被覆盖。")
                else:
                    self._log("Explorer 已结束。")

            # 2) 再写注册表
            n, errs = set_all_promoted(value)
            self._log("已写入 %d 个图标：IsPromoted=%d（%s）" % (n, value, word))

            # 3) 校验（重启前）
            shown, hidden, missing = get_counts()
            self._log("重启前校验：显示 %d / 收纳 %d / 无值 %d" % (shown, hidden, missing))
            for e in errs[:5]:
                self._log("写入失败 " + e)

            # 4) 重新启动 Explorer
            if self.var_restart.get():
                time.sleep(0.4)
                if start_explorer():
                    self._log("Explorer 已启动，等待稳定后二次校验 ...")
                else:
                    self._log("启动 Explorer 失败！请手动 Ctrl+Shift+Esc → 运行新任务 → explorer.exe")

                # 5) 重启后二次校验：检测 Explorer 是否把我们写的值改回去了
                time.sleep(4)
                shown2, hidden2, missing2 = get_counts()
                self._log("重启后校验：显示 %d / 收纳 %d / 无值 %d" % (shown2, hidden2, missing2))
                cur = shown2 if value == 1 else hidden2
                if cur < n * 0.8:
                    self._log("提示：Explorer 似乎改回了部分设置，可尝试点「重置托盘图标缓存」后再操作一次。")
                else:
                    self._log("完成，请查看任务栏右下角。")
            else:
                self._log("未重启 Explorer，设置将在下次重启 Explorer 后生效。")
        except Exception as e:
            self._log("出错：%s" % e)
        finally:
            # 兜底：任何异常都不能把 Explorer 留在"已结束"状态，否则桌面会黑屏
            try:
                if not explorer_running():
                    self._log("检测到 Explorer 未运行，正在恢复 ...")
                    start_explorer()
            except Exception:
                pass
            self.root.after(0, lambda: (self.refresh(), self.set_busy(False)))

    def _log(self, msg):
        self.root.after(0, lambda: self.write_log(msg))

    def restart_only(self):
        if not messagebox.askyesno("确认", "将重启 Explorer（任务栏闪一下），继续吗？"):
            return
        spawn_watchdog(10)
        kill_explorer()
        time.sleep(0.4)
        start_explorer()
        self.write_log("Explorer 已重启。")
        self.refresh()

    def reset_cache(self):
        if not messagebox.askyesno("确认",
                                   "将清除托盘图标缓存并重启 Explorer（任务栏闪一下）。\n"
                                   "这是排查用的操作，正常情况无需点击。继续吗？"):
            return
        removed = reset_tray_cache()
        self.write_log("已清除缓存项：%s" % (", ".join(removed) if removed else "无（本就不存在）"))
        spawn_watchdog(10)
        kill_explorer()
        time.sleep(0.4)
        start_explorer()
        self.write_log("Explorer 已重启。")
        self.refresh()

    def open_settings(self):
        try:
            os.startfile("ms-settings:taskbar")
            self.write_log("已打开系统托盘设置页。")
        except Exception as e:
            self.write_log("打开失败：%s" % e)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
