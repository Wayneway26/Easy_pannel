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
import sys
import time
import tkinter as tk
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))


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
        if not core.kill_explorer():
            messagebox.showwarning("托盘图标助手", "Explorer 未能完全结束，修改可能不生效。")
        n, errs = core.set_all_promoted(want)
        time.sleep(0.4)
        core.start_explorer()
        time.sleep(3)

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
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
