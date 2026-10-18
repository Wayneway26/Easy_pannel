# -*- coding: utf-8 -*-
"""
托盘图标助手 GUI
- 一键全部展开：把所有托盘图标显示到任务栏
- 一键恢复收纳：把所有托盘图标收进右下角倒三角

关键点 1：必须先结束 Explorer，再写注册表，最后再启动 Explorer。
（Explorer 退出时会把内存中的托盘状态回写注册表，顺序颠倒会导致修改被覆盖）

关键点 2：启动 Explorer 必须脱离父进程，否则脚本退出时会被连带结束，导致桌面黑屏。
"""
import os
import ctypes
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import winreg

NOTIFY = r"Control Panel\NotifyIconSettings"
SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
EXPLORER = os.path.join(SYSROOT, "explorer.exe")

# ---------------- 配色 ----------------
BG = "#F5F4F1"        # 窗口底色
CARD = "#FFFFFF"      # 卡片
LINE = "#E4E1DA"      # 描边
TEXT = "#26262A"      # 主文字
MUTED = "#8B8880"     # 次要文字
BRAND = "#E60651"     # 品牌色
LOG_BG = "#1E1F22"    # 日志底色
LOG_FG = "#D6D6D8"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


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


def windows_label():
    """返回系统版本描述，用于界面副标题"""
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        build = int(winreg.QueryValueEx(k, "CurrentBuild")[0])
        winreg.CloseKey(k)
        return "Windows 11" if build >= 22000 else "Windows 10"
    except Exception:
        return "Windows"


# ---------------- 自绘控件 ----------------

def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形（平滑多边形近似）"""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class RoundedButton(tk.Canvas):
    """圆角按钮：三种风格，带悬停与按下反馈，可禁用"""

    STYLES = {
        "primary":   dict(fill=BRAND, hover="#CF0548", press="#B4043E",
                          text="#FFFFFF", border="", font_weight="bold"),
        "secondary": dict(fill="#FFFFFF", hover="#F7F5F0", press="#EFEBE3",
                          text=TEXT, border=LINE, font_weight="normal"),
        "ghost":     dict(fill="#FFFFFF", hover="#F7F5F0", press="#EFEBE3",
                          text="#55534E", border=LINE, font_weight="normal"),
    }

    def __init__(self, parent, text, command=None, kind="primary",
                 bg=BG, height=46, font_size=10):
        tk.Canvas.__init__(self, parent, height=height, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._command = command
        self._style = dict(self.STYLES.get(kind, self.STYLES["primary"]))
        self._h = height
        self._fs = font_size
        self._state = "normal"
        self._shape = None
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, _=None):
        self.delete("all")
        w = self.winfo_width() or 10
        h = self._h
        if self._state == "disabled":
            fill, border, fg = "#ECE9E3", LINE, "#A9A6A0"
        else:
            fill, border, fg = self._style["fill"], self._style["border"], self._style["text"]
        _round_rect(self, 1, 1, w - 1, h - 1, 9, fill=fill,
                    outline=border if border else fill, width=1)
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=fg,
                         font=(FONT, self._fs, self._style["font_weight"]))

    def _paint(self, fill):
        self.delete("all")
        w = self.winfo_width() or 10
        h = self._h
        border = self._style["border"]
        _round_rect(self, 1, 1, w - 1, h - 1, 9, fill=fill,
                    outline=border if border else fill, width=1)
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=self._style["text"],
                         font=(FONT, self._fs, self._style["font_weight"]))

    def _on_enter(self, _):
        if self._state == "normal":
            self._paint(self._style["hover"])

    def _on_leave(self, _):
        if self._state == "normal":
            self._paint(self._style["fill"])

    def _on_press(self, _):
        if self._state == "normal":
            self._paint(self._style["press"])

    def _on_release(self, _):
        if self._state != "normal":
            return
        self._paint(self._style["hover"])
        if self._command:
            self._command()

    def set_state(self, state):
        self._state = state
        self.configure(cursor="hand2" if state == "normal" else "arrow")
        self._draw()


class Toggle(tk.Canvas):
    """开关：46×26 胶囊形，点击切换"""

    def __init__(self, parent, variable, bg=BG):
        tk.Canvas.__init__(self, parent, width=46, height=26, bg=bg,
                           highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.bind("<Button-1>", self._toggle)
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        track = BRAND if on else "#D3D0C9"
        _round_rect(self, 1, 3, 45, 23, 10, fill=track, outline=track)
        cx = 34 if on else 12
        self.create_oval(cx - 8, 5, cx + 8, 21, fill="#FFFFFF", outline="")

    def _toggle(self, _):
        self.var.set(not self.var.get())


# ---------------- 主界面 ----------------

class App(object):
    def __init__(self, root):
        self.root = root
        root.title("托盘图标助手")
        root.geometry("640x656")
        root.resizable(False, False)
        root.configure(bg=BG)

        ico = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "assets", "tray-expand.ico")
        if os.path.exists(ico):
            try:
                root.iconbitmap(default=ico)
            except tk.TclError:
                pass

        self.count = {"shown": 0, "hidden": 0, "missing": 0}
        self._build_header()
        self._build_stats()
        self._build_actions()
        self._build_option()
        self._build_ghost()
        self._build_log()
        self._build_footer()

        self.refresh()
        self.write_log("就绪。修改仅影响当前用户，可随时反向还原。")

    # ---- 顶部标题 ----
    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=24, pady=(22, 0))

        bar = tk.Frame(head, bg=BRAND, width=4, height=38)
        bar.pack(side="left", fill="y", padx=(0, 12))
        bar.pack_propagate(False)

        box = tk.Frame(head, bg=BG)
        box.pack(side="left", fill="x", expand=True)
        tk.Label(box, text="托盘图标助手", bg=BG, fg=TEXT,
                 font=(FONT, 16, "bold"), anchor="w").pack(fill="x")
        self.subtitle = tk.Label(box, text="", bg=BG, fg=MUTED,
                                 font=(FONT, 9), anchor="w")
        self.subtitle.pack(fill="x", pady=(3, 0))

        tk.Label(head, text="v1.0", bg=BG, fg="#B5B2AA",
                 font=(FONT, 9)).pack(side="right", anchor="n")

    # ---- 三张统计卡片 ----
    def _build_stats(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=24, pady=(16, 0))
        for i in range(3):
            wrap.grid_columnconfigure(i, weight=1, uniform="stat")

        self.stat_labels = {}
        specs = [("shown", "显示在任务栏", "#0F6E56"),
                 ("hidden", "收在溢出区", "#B45309"),
                 ("missing", "暂无设置值", "#6B6B72")]
        for i, (key, text, color) in enumerate(specs):
            card = tk.Frame(wrap, bg=CARD, highlightbackground=LINE,
                            highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 5, 0 if i == 2 else 5))
            num = tk.Label(card, text="0", bg=CARD, fg=color,
                           font=(FONT, 20, "bold"), anchor="w")
            num.pack(fill="x", padx=14, pady=(12, 0))
            tk.Label(card, text=text, bg=CARD, fg=MUTED,
                     font=(FONT, 8), anchor="w").pack(fill="x", padx=14, pady=(0, 12))
            self.stat_labels[key] = num

    # ---- 两个主操作 ----
    def _build_actions(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=24, pady=(14, 0))
        row.grid_columnconfigure(0, weight=1, uniform="act")
        row.grid_columnconfigure(1, weight=1, uniform="act")

        self.btn_expand = RoundedButton(row, "一键全部展开", self._ask_expand,
                                        kind="primary", height=48, font_size=10)
        self.btn_expand.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.btn_collapse = RoundedButton(row, "一键恢复收纳", self._ask_collapse,
                                          kind="secondary", height=48, font_size=10)
        self.btn_collapse.grid(row=0, column=1, sticky="ew", padx=(5, 0))

    # ---- 选项开关 ----
    def _build_option(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=24, pady=(14, 0))

        self.var_restart = tk.BooleanVar(value=True)
        tk.Label(row, text="操作后重启 Explorer", bg=BG, fg=TEXT,
                 font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        Toggle(row, self.var_restart).grid(row=0, column=1, padx=(10, 8))
        tk.Label(row, text="先结束 → 写注册表 → 再启动，确保修改不被覆盖",
                 bg=BG, fg=MUTED, font=(FONT, 8)).grid(row=0, column=2, sticky="w")

        tk.Label(self.root,
                 text="新装的软件图标若又躲进倒三角，再点一次「一键全部展开」即可。",
                 bg=BG, fg=MUTED, font=(FONT, 8), anchor="w").pack(
            fill="x", padx=24, pady=(6, 0))

    # ---- 辅助按钮 ----
    def _build_ghost(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=24, pady=(12, 0))
        items = [("刷新状态", self.refresh),
                 ("系统托盘设置", self.open_settings),
                 ("仅重启 Explorer", self.restart_only),
                 ("重置图标缓存", self.reset_cache)]
        for i, (text, cmd) in enumerate(items):
            row.grid_columnconfigure(i, weight=1, uniform="ghost")
            RoundedButton(row, text, cmd, kind="ghost", height=34,
                          font_size=8).grid(
                row=0, column=i, sticky="ew",
                padx=(0 if i == 0 else 4, 0 if i == 3 else 4))

    # ---- 日志 ----
    def _build_log(self):
        box = tk.Frame(self.root, bg=BG)
        box.pack(fill="both", expand=True, padx=24, pady=(16, 0))

        tk.Label(box, text="运行日志", bg=BG, fg=MUTED,
                 font=(FONT, 8), anchor="w").pack(fill="x", pady=(0, 6))

        panel = tk.Frame(box, bg=LOG_BG)
        panel.pack(fill="both", expand=True)

        self.log = tk.Text(panel, bg=LOG_BG, fg=LOG_FG, bd=0,
                           highlightthickness=0, wrap="word",
                           font=(MONO, 9), padx=14, pady=12,
                           insertbackground=LOG_FG, height=9)
        self.log.pack(side="left", fill="both", expand=True)

        # 系统滚动条两端自带白色箭头，和深色面板不搭，改为滚轮滚动
        self.log.bind("<MouseWheel>", self._on_wheel)

        self.log.tag_configure("time", foreground="#6E6F76")
        self.log.tag_configure("info", foreground="#D2D2D6")
        self.log.tag_configure("ok", foreground="#7ED0A5")
        self.log.tag_configure("warn", foreground="#F2C46B")
        self.log.tag_configure("err", foreground="#F08C8C")
        self.log.configure(state="disabled")

    def _on_wheel(self, event):
        if self.log.yview() == (0.0, 1.0):      # 内容不足一屏时不动
            return "break"
        self.log.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _build_footer(self):
        tk.Label(self.root,
                 text="仅修改当前用户的显示设置 · 不写系统级配置 · 不联网",
                 bg=BG, fg="#B5B2AA", font=(FONT, 8), anchor="w").pack(
            fill="x", padx=24, pady=(10, 16))

    # ---- 逻辑 ----
    def write_log(self, msg, level=None):
        if level is None:
            if msg.startswith("警告") or msg.startswith("提示"):
                level = "warn"
            elif any(k in msg for k in ("失败", "出错", "错误")):
                level = "err"
            elif any(k in msg for k in ("完成", "已启动", "已写入", "已重启", "已结束")):
                level = "ok"
            else:
                level = "info"
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S") + "   ", "time")
        self.log.insert("end", msg + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def refresh(self):
        shown, hidden, missing = get_counts()
        total = shown + hidden + missing
        self.count = {"shown": shown, "hidden": hidden, "missing": missing}
        for key, value in self.count.items():
            self.stat_labels[key].config(text=str(value))
        self.subtitle.config(text="共 %d 个托盘图标记录 · %s" % (total, windows_label()))
        return shown, hidden, missing

    def set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.btn_expand.set_state(state)
        self.btn_collapse.set_state(state)

    def _ask_expand(self):
        self.do_action(1)

    def _ask_collapse(self):
        self.do_action(0)

    def do_action(self, value):
        word = "展开到任务栏" if value == 1 else "收进倒三角"
        if not messagebox.askyesno("确认", "将把所有托盘图标%s，确定吗？" % word,
                                   parent=self.root):
            return
        if self.var_restart.get():
            if not messagebox.askyesno("确认",
                                       "需要重启 Explorer：任务栏会短暂消失后自动恢复，\n"
                                       "已打开的窗口不受影响。继续吗？",
                                       parent=self.root):
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
                    self._log("提示：Explorer 似乎改回了部分设置，可尝试点「重置图标缓存」后再操作一次。")
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
        if not messagebox.askyesno("确认", "将重启 Explorer（任务栏闪一下），继续吗？",
                                   parent=self.root):
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
                                   "这是排查用的操作，正常情况无需点击。继续吗？",
                                   parent=self.root):
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
