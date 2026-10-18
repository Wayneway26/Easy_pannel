# -*- coding: utf-8 -*-
"""
托盘动作入口（供 Win+X 菜单等外部入口调用）
用法：
    pythonw TrayAction.pyw expand      一键全部展开（带确认弹窗）
    pythonw TrayAction.pyw collapse    一键恢复收纳（带确认弹窗）
不打开主界面，执行完给出结果提示。
"""
import importlib.util
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG = os.path.join(HERE, "ExplorerWatchdog.pyw")


def spawn_watchdog(grace=10):
    """在结束 Explorer 之前先放出看门狗：若本脚本中途异常退出导致桌面黑屏，由它兜底恢复。"""
    try:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            cand = exe[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(cand):
                exe = cand
        subprocess.Popen([exe, WATCHDOG, str(grace)],
                         close_fds=True,
                         creationflags=0x00000008 | 0x00000200,   # DETACHED | NEW_GROUP
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def load_core():
    """加载同目录 TrayGUI.pyw 里的核心函数，避免重复实现"""
    spec = importlib.util.spec_from_file_location("tray_core", os.path.join(HERE, "TrayGUI.pyw"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "expand").lower()
    want = 0 if action == "collapse" else 1
    word = "收进右下角倒三角" if want == 0 else "展开到任务栏"

    core = load_core()

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    if not messagebox.askyesno("托盘图标助手", "确定把所有托盘图标%s吗？\n任务栏会短暂闪一下。" % word):
        root.destroy()
        return

    try:
        spawn_watchdog(10)          # 关键：先挂兜底，再动 Explorer
        if not core.kill_explorer():
            messagebox.showwarning("托盘图标助手", "Explorer 未能完全结束，修改可能不生效。")
        n, errs = core.set_all_promoted(want)
        time.sleep(0.4)
        started = core.start_explorer()       # 内部已做"脱离父进程 + 启动校验 + 重试"
        if not started:
            messagebox.showerror(
                "托盘图标助手",
                "Explorer 未能启动，桌面可能黑屏。\n正在再试一次…\n\n"
                "若仍无效：按 Ctrl+Shift+Esc → 文件 → 运行新任务 → 输入 explorer.exe")
            core.start_explorer()
            return
        time.sleep(2.5)

        shown, hidden, missing = core.get_counts()
        if want == 1:
            ok = shown >= n * 0.8
        else:
            ok = hidden >= n * 0.8

        if ok:
            detail = "已显示 %d 个，收在溢出区 %d 个" % (shown, hidden) if want == 1 \
                else "已收纳 %d 个，仍在任务栏 %d 个" % (hidden, shown)
            messagebox.showinfo("托盘图标助手", "完成：%s。\n%s" % (word, detail))
        else:
            messagebox.showwarning(
                "托盘图标助手",
                "设置似乎被 Explorer 改回了。\n可打开主界面点「重置托盘图标缓存」后再试。")
    except Exception as e:
        messagebox.showerror("托盘图标助手", "执行失败：%s" % e)
    finally:
        # 无论如何都要保证 Explorer 回到运行状态，否则桌面会黑屏
        try:
            if not core.explorer_running():
                core.start_explorer()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
