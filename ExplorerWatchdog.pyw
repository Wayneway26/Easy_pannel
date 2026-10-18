# -*- coding: utf-8 -*-
"""
Explorer 看门狗 —— 防止"桌面变黑、任务栏消失"

用法（由主程序自动调用，也可手动执行）：
    pythonw ExplorerWatchdog.pyw [宽限秒数]

工作方式：
  1. 在宽限期内（默认 10 秒）持续观察。只要 Explorer 被主流程正常拉起，就静默退出；
  2. 宽限期结束仍没有 Explorer（说明主流程崩溃/被中断），立即强制拉起并持续重试。

为什么不在 Explorer 一消失就拉起：主流程需要一段"Explorer 不在"的窗口来写注册表，
过早拉起会用内存状态覆盖刚写入的值。默认 10 秒已远大于实际操作耗时（约 1 秒）。
"""
import ctypes
import os
import subprocess
import sys
import time

SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
EXPLORER = os.path.join(SYSROOT, "explorer.exe")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_BASE = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def log(msg):
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def explorer_running():
    try:
        out = subprocess.run(["tasklist", "/fi", "imagename eq explorer.exe"],
                             capture_output=True, timeout=10).stdout
        return b"explorer.exe" in out.lower()
    except Exception:
        try:
            return bool(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None))
        except Exception:
            return False


def start_explorer():
    for fl in (_BASE | CREATE_BREAKAWAY_FROM_JOB, _BASE, 0):
        try:
            subprocess.Popen([EXPLORER], close_fds=True, creationflags=fl,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            break
        except OSError:
            continue
    else:
        try:                                  # 兜底：交给 cmd start
            subprocess.Popen([os.path.join(SYSROOT, "System32", "cmd.exe"),
                              "/c", "start", "", EXPLORER],
                             close_fds=True, creationflags=_BASE,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass
    for _ in range(24):
        time.sleep(0.25)
        if explorer_running():
            return True
    return False


def main():
    grace = 10.0
    if len(sys.argv) > 1:
        try:
            grace = float(sys.argv[1])
        except ValueError:
            pass

    # 阶段一：宽限期内等主流程把 Explorer 拉起来
    deadline = time.time() + grace
    while time.time() < deadline:
        if explorer_running():
            return                      # 主流程正常完成，静默退出
        time.sleep(0.4)

    # 阶段二：超时仍未恢复 -> 补救
    log("宽限期 %.0fs 结束仍无 Explorer，开始强制恢复" % grace)
    for attempt in range(1, 6):
        if start_explorer():
            log("第 %d 次尝试后 Explorer 已恢复" % attempt)
            return
        log("第 %d 次尝试失败" % attempt)
        time.sleep(1)
    log("恢复失败，请手动 Ctrl+Shift+Esc -> 运行新任务 -> explorer.exe")


if __name__ == "__main__":
    main()
