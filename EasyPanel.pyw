# -*- coding: utf-8 -*-
"""
EasyPanel —— Dock 与托盘图标的统一控制面板（Windows 11 风格）

视觉规范照 Windows 11「设置」来：
  · 底色 #F3F3F3、内容卡片 #FBFBFB 带 1px 描边、控件圆角 4px
  · 强调色 #0067C0，正文微软雅黑，日志等宽 Consolas
  · 左侧导航（选中项白底 + 左侧强调色竖条）、右上角最小化/关闭
界面全部用 Canvas 自绘，不依赖任何第三方 UI 库，保持项目「绿色单文件」的特点。

两个子工具：
  · Dock        —— 独立进程（dock/EasyDock.pyw），本面板负责启停 + 改配置
  · 托盘图标    —— 直接复用 TrayGUI.pyw 里的核心函数，后台线程执行
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import byref, c_ulong, c_void_p
from tkinter import filedialog

HERE = os.path.dirname(os.path.abspath(__file__))
DOCK_DIR = os.path.join(HERE, "dock")
DOCK_SCRIPT = os.path.join(DOCK_DIR, "EasyDock.pyw")
DOCK_CONFIG = os.path.join(DOCK_DIR, "config.json")
DOCK_PID = os.path.join(DOCK_DIR, "dock.pid")
TRAY_SCRIPT = os.path.join(HERE, "TrayGUI.pyw")
ICON_CACHE = os.path.join(HERE, "cache", "panel-icons")

VERSION = "1.0.0"

# ---------------------------------------------------------------- 调色板
MAGIC = "#010203"            # 色键：窗口圆角外的部分靠它抠成真透明
BG = "#F3F3F3"
CARD = "#FBFBFB"
CARD_LINE = "#E3E3E3"
ROW_HOVER = "#EFEFEF"
TEXT = "#1B1B1B"
MUTED = "#616161"
FAINT = "#8A8A8A"
ACCENT = "#0067C0"
ACCENT_HOVER = "#1A76C9"
ACCENT_PRESS = "#005BA6"
BTN = "#FDFDFD"
BTN_LINE = "#D6D6D6"
BTN_HOVER = "#F4F4F4"
BTN_PRESS = "#EBEBEB"
TRACK_OFF = "#9A9A9A"
DANGER = "#C42B1C"
SEG_BG = "#EDEDED"
LOG_BG = "#1F1F1F"
LOG_FG = "#D2D2D2"

UI = "Microsoft YaHei UI"
MONO = "Consolas"

W, H = 780, 780
NAV_W = 208
TITLE_H = 48
CX0, CX1 = 232, 756


def f(size, weight="normal", family=UI):
    """负数字号 = 像素，跨缩放看起来更稳"""
    return (family, -int(size), weight)


# ---------------------------------------------------------------- 基础图元
def round_rect(x1, y1, x2, y2, r, steps=6):
    """生成圆角矩形的多边形点序列"""
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = []
    for cx, cy, a0, a1 in ((x2 - r, y1 + r, -90, 0), (x2 - r, y2 - r, 0, 90),
                           (x1 + r, y2 - r, 90, 180), (x1 + r, y1 + r, 180, 270)):
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


def tr_round(x1, y1, x2, y2, r=8, steps=6):
    """只有右上角是圆角的矩形（标题栏按钮贴窗口圆角用）"""
    pts = [x1, y1, x2 - r, y1]
    for i in range(steps + 1):
        a = math.radians(-90 + 90.0 * i / steps)
        pts.extend([x2 - r + r * math.cos(a), y1 + r + r * math.sin(a)])
    pts.extend([x2, y2, x1, y2])
    return pts


def load_module(path, name):
    """把同目录的 .pyw 当模块加载，复用里面的逻辑"""
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:                                    # noqa: BLE001
        return None


def pythonw():
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe or "pythonw.exe"


# ---------------------------------------------------------------- 控件
class Widget:
    """所有自绘控件的基类：统一用矩形热区做命中判断"""
    box = (0, 0, 0, 0)

    def hit(self, x, y):
        x1, y1, x2, y2 = self.box
        return x1 <= x <= x2 and y1 <= y <= y2

    def hover(self, on):
        pass

    def click(self, x, y):
        pass


class Btn(Widget):
    def __init__(self, cv, x, y, w, h, text, cmd, kind="secondary", radius=4, size=13):
        self.cv = cv
        self.cmd = cmd
        self.kind = kind
        self.enabled = True
        self.box = (x, y, x + w, y + h)
        self.bg = cv.create_polygon(round_rect(x, y, x + w, y + h, radius),
                                    fill=BTN, outline=BTN_LINE, width=1, tags="content")
        self.fg = cv.create_text(x + w / 2.0, y + h / 2.0, text=text,
                                 fill=TEXT, font=f(size), tags="content")
        self._paint("normal")

    def _paint(self, state):
        if not self.enabled:
            fill, line, fg = "#F2F2F2", "#E4E4E4", "#A8A8A8"
        elif self.kind == "primary":
            fill = {"normal": ACCENT, "hover": ACCENT_HOVER, "press": ACCENT_PRESS}[state]
            line, fg = fill, "#FFFFFF"
        elif self.kind == "danger":
            fill = {"normal": BTN, "hover": "#FDEDEB", "press": "#F9DAD6"}[state]
            line, fg = BTN_LINE, DANGER
        else:
            fill = {"normal": BTN, "hover": BTN_HOVER, "press": BTN_PRESS}[state]
            line, fg = BTN_LINE, TEXT
        self.cv.itemconfig(self.bg, fill=fill, outline=line)
        self.cv.itemconfig(self.fg, fill=fg)

    def hover(self, on):
        if self.enabled:
            self._paint("hover" if on else "normal")

    def click(self, x, y):
        if self.enabled and self.cmd:
            self.cmd()

    def set_text(self, s):
        self.cv.itemconfig(self.fg, text=s)

    def set_kind(self, kind):
        self.kind = kind
        self._paint("normal")

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._paint("normal")


class TitleBtn(Widget):
    """标题栏上的最小化 / 关闭"""

    def __init__(self, cv, x, y, kind, cmd):
        self.cv, self.cmd = cv, cmd
        self.kind = kind
        self.danger = kind == "close"
        self.box = (x, y, x + 46, y + 32)
        if self.danger:
            self.bg = cv.create_polygon(tr_round(x, y, x + 46, y + 32, 8),
                                        fill=BG, outline="", tags="content")
        else:
            self.bg = cv.create_rectangle(x, y, x + 46, y + 32, fill=BG, outline="", tags="content")
        cx, cy = x + 23, y + 16
        if self.danger:
            self.marks = [cv.create_line(cx - 5, cy - 5, cx + 5, cy + 5, fill=TEXT, width=1.1, tags="content"),
                          cv.create_line(cx - 5, cy + 5, cx + 5, cy - 5, fill=TEXT, width=1.1, tags="content")]
        else:
            self.marks = [cv.create_line(cx - 5, cy, cx + 5, cy, fill=TEXT, width=1.1, tags="content")]

    def hover(self, on):
        if self.danger:
            self.cv.itemconfig(self.bg, fill=DANGER if on else BG)
            for m in self.marks:
                self.cv.itemconfig(m, fill="#FFFFFF" if on else TEXT)
        else:
            self.cv.itemconfig(self.bg, fill=BTN_HOVER if on else BG)

    def click(self, x, y):
        self.cmd()


class Switch(Widget):
    """Windows 11 开关：40×20 轨道 + 白滑块"""

    def __init__(self, cv, x, y, value, cmd, w=40, h=20):
        self.cv, self.cmd = cv, cmd
        self.value = bool(value)
        self.w, self.h = w, h
        self.box = (x, y, x + w, y + h)
        self.track = cv.create_polygon(round_rect(x, y, x + w, y + h, h / 2.0),
                                       fill=ACCENT, outline="", tags="content")
        self.knob = cv.create_oval(0, 0, 0, 0, fill="#FFFFFF", outline="", tags="content")
        self._paint()

    def _paint(self):
        self.cv.itemconfig(self.track, fill=ACCENT if self.value else TRACK_OFF)
        r = (self.h - 8) / 2.0
        cy = self.box[1] + self.h / 2.0
        cx = (self.box[2] - self.h / 2.0) if self.value else (self.box[0] + self.h / 2.0)
        self.cv.coords(self.knob, cx - r, cy - r, cx + r, cy + r)

    def click(self, x, y):
        self.value = not self.value
        self._paint()
        if self.cmd:
            self.cmd(self.value)


class Seg(Widget):
    """两段式选择器（Dock 主题）"""

    def __init__(self, cv, x, y, w, h, options, index, cmd):
        self.cv, self.cmd = cv, cmd
        self.options = list(options)
        self.index = index
        self.box = (x, y, x + w, y + h)
        self.x, self.y, self.w, self.h = x, y, w, h
        cv.create_polygon(round_rect(x, y, x + w, y + h, 4),
                          fill=SEG_BG, outline="", tags="content")
        self.cells, self.labels = [], []
        seg_w = (w - 4) / float(len(self.options))
        for i, text in enumerate(self.options):
            x1 = x + 2 + i * seg_w
            cell = cv.create_polygon(round_rect(x1, y + 2, x1 + seg_w, y + h - 2, 3),
                                     fill="", outline="", tags="content")
            lb = cv.create_text(x1 + seg_w / 2.0, y + h / 2.0, text=text,
                                fill=MUTED, font=f(12), tags="content")
            self.cells.append(cell)
            self.labels.append(lb)
        self._paint()

    def _paint(self):
        for i, cell in enumerate(self.cells):
            on = i == self.index
            self.cv.itemconfig(cell, fill="#FFFFFF" if on else "")
            self.cv.itemconfig(self.labels[i], fill=TEXT if on else MUTED)

    def click(self, x, y):
        seg_w = (self.w - 4) / float(len(self.options))
        i = int((x - self.x - 2) // seg_w)
        if 0 <= i < len(self.options) and i != self.index:
            self.index = i
            self._paint()
            if self.cmd:
                self.cmd(i)


class Stepper(Widget):
    """[-] 数值 [+]，整块一个热区，按 x 判断左右"""

    def __init__(self, cv, x, y, w, h, text, on_minus, on_plus):
        self.cv = cv
        self.on_minus, self.on_plus = on_minus, on_plus
        self.box = (x, y, x + w, y + h)
        self.w, self.h = w, h
        cv.create_polygon(round_rect(x, y, x + w, y + h, 4),
                          fill="#FFFFFF", outline=BTN_LINE, width=1, tags="content")
        cy = y + h / 2.0
        self.zone = []
        for i, glyph in enumerate(("-", "+")):
            zx = x + (4 if i == 0 else w - 32)
            z = cv.create_polygon(round_rect(zx, y + 3, zx + 28, y + h - 3, 3),
                                  fill="", outline="", tags="content")
            cv.create_line(zx + 9, cy, zx + 19, cy, fill=TEXT, width=1.1, tags="content")
            if glyph == "+":
                cv.create_line(zx + 14, cy - 5, zx + 14, cy + 5, fill=TEXT, width=1.1, tags="content")
            self.zone.append(z)
        self.label = cv.create_text(x + w / 2.0, cy, text=text, fill=TEXT, font=f(13), tags="content")

    def set_text(self, s):
        self.cv.itemconfig(self.label, text=s)

    def hover(self, on):
        pass

    def click(self, x, y):
        if x < self.box[0] + 34:
            self.on_minus()
        elif x > self.box[2] - 34:
            self.on_plus()


class NavItem(Widget):
    """左侧导航项"""

    def __init__(self, cv, x, y, w, h, key, label, icon, cmd):
        self.cv, self.key, self.cmd = cv, key, cmd
        self.box = (x, y, x + w, y + h)
        self.bg = cv.create_polygon(round_rect(x, y, x + w, y + h, 4),
                                    fill="", outline="", tags="ui")
        self.bar = cv.create_polygon(round_rect(x + 4, y + h / 2.0 - 8, x + 7, y + h / 2.0 + 8, 1.5),
                                     fill="", outline="", tags="ui")
        self.icon = nav_icon(cv, x + 26, y + h / 2.0, icon, MUTED)
        self.label = cv.create_text(x + 44, y + h / 2.0, text=label, anchor="w",
                                    fill=TEXT, font=f(13), tags="ui")
        self.active = False

    def set_active(self, on):
        self.active = on
        self.cv.itemconfig(self.bg, fill="#FFFFFF" if on else "", outline=CARD_LINE if on else "")
        self.cv.itemconfig(self.bar, fill=ACCENT if on else "")
        color = ACCENT if on else MUTED
        for i in self.icon[0]:
            self.cv.itemconfig(i, fill=color)
        for i in self.icon[1]:
            self.cv.itemconfig(i, outline=color)
        self.cv.itemconfig(self.label, fill=TEXT if on else MUTED)

    def hover(self, on):
        if not self.active:
            self.cv.itemconfig(self.bg, fill=ROW_HOVER if on else "")

    def click(self, x, y):
        self.cmd(self.key)


class AppRow(Widget):
    """Dock 应用列表里的一行"""

    def __init__(self, cv, x, y, w, h, panel, index):
        self.cv, self.panel, self.index = cv, panel, index
        self.box = (x, y, x + w, y + h)
        self.bg = cv.create_polygon(round_rect(x, y, x + w, y + h, 4),
                                    fill="", outline="", tags="content")
        item = panel.dock_cfg["items"][index]
        self.thumb = panel.row_icon(item, x + 6, y + (h - 22) / 2.0)
        cv.create_text(x + 40, y + h / 2.0, text=item.get("name") or "未命名",
                       anchor="w", fill=TEXT, font=f(12.5), tags="content")
        self.btns = []
        for i, (glyph, cb, title) in enumerate((
                ("up", lambda: panel.move_item(index, -1), "上移"),
                ("down", lambda: panel.move_item(index, +1), "下移"),
                ("x", lambda: panel.remove_item(index), "移除"))):
            self.btns.append(SmallIcon(cv, x + w - 84 + i * 28, y + (h - 24) / 2.0, 24, glyph, cb))

    def hover(self, on):
        self.cv.itemconfig(self.bg, fill=ROW_HOVER if on else "")

    def click(self, x, y):
        pass


class SmallIcon(Widget):
    """行内的小图标按钮：↑ ↓ ×"""

    def __init__(self, cv, x, y, size, glyph, cmd):
        self.cv, self.cmd = cv, cmd
        self.box = (x, y, x + size, y + size)
        self.bg = cv.create_polygon(round_rect(x, y, x + size, y + size, 3),
                                    fill="", outline="", tags="content")
        cx, cy = x + size / 2.0, y + size / 2.0
        col = MUTED if glyph != "x" else FAINT
        if glyph == "up":
            self.mark = cv.create_line(cx, cy + 4, cx, cy - 4, fill=col, width=1.2,
                                       arrow="last", arrowshape=(5, 6, 2.5), tags="content")
        elif glyph == "down":
            self.mark = cv.create_line(cx, cy - 4, cx, cy + 4, fill=col, width=1.2,
                                       arrow="last", arrowshape=(5, 6, 2.5), tags="content")
        else:
            self.mark = [cv.create_line(cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, fill=col, width=1.2, tags="content"),
                         cv.create_line(cx - 3.5, cy + 3.5, cx + 3.5, cy - 3.5, fill=col, width=1.2, tags="content")]

    def hover(self, on):
        self.cv.itemconfig(self.bg, fill="#E6E6E6" if on else "")

    def click(self, x, y):
        self.cmd()


def nav_icon(cv, cx, cy, kind, color):
    """导航图标全部几何自绘，不依赖图标字体。
    返回 (实心图形, 描边图形) —— 两类要用 itemconfig 的不同字段改色。"""
    filled, stroked = [], []
    if kind == "dock":
        for dx in (-10, -3, 4):
            filled.append(cv.create_polygon(round_rect(cx + dx, cy - 8, cx + dx + 6, cy, 1.5),
                                            fill=color, outline="", tags="ui"))
        filled.append(cv.create_polygon(round_rect(cx - 10, cy + 4, cx + 10, cy + 7, 1.5),
                                        fill=color, outline="", tags="ui"))
    elif kind == "tray":
        filled.append(cv.create_polygon([cx - 7, cy - 7, cx + 7, cy - 7, cx, cy],
                                        fill=color, outline="", tags="ui"))
        for dx in (-6, 0, 6):
            filled.append(cv.create_oval(cx + dx - 1.5, cy + 4, cx + dx + 1.5, cy + 7,
                                         fill=color, outline="", tags="ui"))
    else:                                              # info：圆环 + 点 + 竖线
        stroked.append(cv.create_oval(cx - 8, cy - 8, cx + 8, cy + 8,
                                      fill="", outline=color, width=1.35, tags="ui"))
        filled.append(cv.create_oval(cx - 1.3, cy - 5.6, cx + 1.3, cy - 3.0,
                                     fill=color, outline="", tags="ui"))
        filled.append(cv.create_line(cx, cy - 1.4, cx, cy + 5, fill=color, width=1.5, tags="ui"))
    return filled, stroked


# ---------------------------------------------------------------- 主面板
class Panel:
    def __init__(self, root):
        self.root = root
        self.dock = load_module(DOCK_SCRIPT, "easy_dock")
        self.tray = load_module(TRAY_SCRIPT, "tray_core")
        self.dock_cfg = self._read_dock_cfg()
        self.page = "dock"
        self.widgets = []
        self.proxy = []

        self.hot = None
        self.drag = None
        self.busy = False
        self.restart_after = True
        self.row_icons = {}
        self.log_box = None
        self.log_history = []
        self._alive_cache = None

        self._setup_window()
        self._draw_chrome()
        self.show_page("dock")
        self.root.after(600, self._poll_dock_state)

    # -- 基础 ------------------------------------------------------
    def _read_dock_cfg(self):
        if self.dock:
            try:
                return self.dock.load_config()
            except Exception:                          # noqa: BLE001
                pass
        return {"icon_size": 48, "gap": 10, "padding": 10, "radius": 18,
                "zoom": 1.75, "bg": "#1F1F24", "border": "#3A3A42",
                "topmost": True, "items": []}

    def save_dock_cfg(self):
        if self.dock:
            try:
                self.dock.save_config(self.dock_cfg)
                return
            except Exception:                          # noqa: BLE001
                pass
        with open(DOCK_CONFIG, "w", encoding="utf-8") as fh:
            json.dump(self.dock_cfg, fh, ensure_ascii=False, indent=2)

    def _setup_window(self):
        r = self.root
        r.title("EasyPanel")
        r.overrideredirect(True)
        r.configure(bg=MAGIC)
        try:
            r.wm_attributes("-transparentcolor", MAGIC)
        except tk.TclError:
            pass
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x, y = (sw - W) // 2, max(20, (sh - H) // 2 - 20)
        r.geometry("%dx%d+%d+%d" % (W, H, x, y))
        try:                                           # 任务栏图标复位（overrideredirect 会藏起来）
            r.wm_attributes("-toolwindow", False)
        except tk.TclError:
            pass

        self.cv = tk.Canvas(r, width=W, height=H, bg=MAGIC,
                            highlightthickness=0, bd=0, takefocus=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Motion>", self._on_motion)
        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)
        self.cv.bind("<ButtonRelease-1>", self._on_release)
        self.cv.bind("<Button-3>", lambda e: None)

    # -- 外壳 ------------------------------------------------------
    def _draw_chrome(self):
        cv = self.cv
        cv.create_polygon(round_rect(0, 0, W, H, 8), fill=BG, outline="#D5D5D5",
                          width=1, tags="chrome")

        # 标题栏
        cv.create_polygon(round_rect(20, 16, 40, 36, 5), fill=ACCENT, outline="", tags="chrome")
        cv.create_polygon(round_rect(25, 21, 31, 27, 1.5), fill="#FFFFFF", outline="", tags="chrome")
        cv.create_polygon(round_rect(25, 30, 31, 34, 1.2), fill="#FFFFFF", outline="", tags="chrome")
        cv.create_polygon(round_rect(33, 21, 35, 34, 1.0), fill="#FFFFFF", outline="", tags="chrome")
        cv.create_text(50, 26, text="EasyPanel", anchor="w", fill=TEXT, font=f(13, "bold"),
                       tags="chrome")

        self.widgets.append(TitleBtn(cv, W - 92, 8, "min", self.minimize))
        self.widgets.append(TitleBtn(cv, W - 46, 8, "close", self.quit_app))

        # 左侧导航
        self.nav = {}
        y = TITLE_H + 16
        for key, label, icon in (("dock", "Dock", "dock"),
                                 ("tray", "托盘图标", "tray"),
                                 ("about", "关于", "info")):
            item = NavItem(cv, 12, y, NAV_W - 24, 38, key, label, icon, self.show_page)
            self.nav[key] = item
            self.widgets.append(item)
            y += 40

        cv.create_text(16, H - 22, text="v" + VERSION, anchor="w", fill=FAINT,
                       font=f(11), tags="chrome")

    # -- 页面切换 --------------------------------------------------
    def show_page(self, key):
        self.page = key
        for w in self.proxy:                           # 页面内的 Text 等真实控件
            try:
                w.destroy()
            except Exception:                          # noqa: BLE001
                pass
        self.proxy = []
        self.log_box = None
        self.widgets = [w for w in self.widgets if not isinstance(w, (Btn, Switch, Seg, Stepper, AppRow, SmallIcon))]
        self.cv.delete("content")
        self.hot = None
        for k, item in self.nav.items():
            item.set_active(k == key)
        {"dock": self._page_dock, "tray": self._page_tray,
         "about": self._page_about}[key]()

    # -- 通用绘制小工具 --------------------------------------------
    def card(self, y, h, title=None, pad=14):
        cv = self.cv
        cv.create_polygon(round_rect(CX0, y, CX1, y + h, 6), fill=CARD,
                          outline=CARD_LINE, width=1, tags="content")
        if title:
            cv.create_text(CX0 + pad, y + 18, text=title, anchor="w", fill=TEXT,
                           font=f(12.5, "bold"), tags="content")
        return y

    def label(self, x, y, text, size=12.5, color=TEXT, weight="normal", anchor="w"):
        return self.cv.create_text(x, y, text=text, anchor=anchor, fill=color,
                                   font=f(size, weight), tags="content")

    def page_head(self, title, subtitle):
        self.label(CX0, 74, title, 22, TEXT, "bold")
        self.label(CX0, 102, subtitle, 12, MUTED)

    # ---------------------------------------------------------- Dock
    def _page_dock(self):
        cfg = self.dock_cfg
        self.page_head("Dock", "屏幕底部的应用启动栏 · 悬停放大 · 单击启动")

        # --- 状态卡 ---
        self.card(120, 80)
        cv = self.cv
        cv.create_polygon(round_rect(CX0 + 14, 138, CX0 + 58, 182, 9),
                          fill="#2B2B31", outline="", tags="content")
        for i in range(3):
            cv.create_polygon(round_rect(CX0 + 22 + i * 11, 152, CX0 + 30 + i * 11, 168, 2),
                              fill="#FFFFFF", outline="", tags="content")
        cv.create_polygon(round_rect(CX0 + 22, 172, CX0 + 50, 175, 1.5),
                          fill="#7A7A85", outline="", tags="content")
        alive = self.dock_pid() is not None
        self.label(CX0 + 70, 152, "Easy Dock", 13.5, TEXT, "bold")
        self.status_label = self.label(
            CX0 + 70, 172,
            ("正在运行 · %d 个应用" % len(cfg.get("items") or [])) if alive
            else "未运行 · 点「启动」显示到桌面底部", 11.5, MUTED)

        self.btn_dock = Btn(cv, CX1 - 16 - 96, 144, 96, 32,
                            "关闭" if alive else "启动",
                            self.toggle_dock, "secondary" if alive else "primary")
        self.widgets.append(self.btn_dock)

        # --- 外观卡 ---
        self.card(212, 180, "外观")
        rows = 254
        self.label(CX0 + 14, rows + 13, "图标大小", 12.5)
        self.label(CX0 + 14, rows + 31, "每个图标的边长（像素）", 10.5, FAINT)
        self.step_size = Stepper(cv, 624, rows, 116, 30, "%d px" % cfg["icon_size"],
                                 lambda: self.bump("icon_size", -4), lambda: self.bump("icon_size", 4))
        self.widgets.append(self.step_size)

        rows += 46
        self.label(CX0 + 14, rows + 13, "悬停放大", 12.5)
        self.label(CX0 + 14, rows + 31, "鼠标移上去时放大的倍数", 10.5, FAINT)
        self.step_zoom = Stepper(cv, 624, rows, 116, 30, "%.2f×" % float(cfg.get("zoom", 1.75)),
                                 lambda: self.bump("zoom", -0.25), lambda: self.bump("zoom", 0.25))
        self.widgets.append(self.step_zoom)

        rows += 46
        self.label(CX0 + 14, rows + 13, "Dock 主题", 12.5)
        theme = "dark" if str(cfg.get("bg", "")).upper() in ("#1F1F24", "#1F1F1F") else "light"
        self.seg_theme = Seg(cv, 608, rows + 2, 132, 28, ["深色", "浅色"],
                             0 if theme == "dark" else 1, self.set_theme)
        self.widgets.append(self.seg_theme)

        # --- 应用列表 ---
        self.card(400, 356)
        items = cfg.get("items") or []
        self.label(CX0 + 14, 418, "Dock 中的应用", 12.5, TEXT, "bold")
        self.label(CX1 - 14, 418,
                   ("%d 个（仅显示前 8 个）" % len(items)) if len(items) > 8
                   else "%d 个" % len(items), 11, FAINT, anchor="e")

        max_rows = 8
        top = 434
        row_h = 33
        for i, _item in enumerate(items[:max_rows]):
            self.widgets.append(AppRow(cv, CX0 + 10, top + i * row_h, CX1 - CX0 - 20,
                                       row_h, self, i))

        self.widgets.append(Btn(cv, CX0 + 14, 706, 108, 30, "添加应用…",
                                self.add_item, "secondary", 4, 12))
        self.widgets.append(Btn(cv, CX0 + 130, 706, 92, 30, "重新启动",
                                self.restart_dock, "secondary", 4, 12))

    # ---------------------------------------------------------- 托盘
    def _page_tray(self):
        self.page_head("托盘图标", "Windows 11 通知区域 · 一键展开全部图标")
        cv = self.cv

        shown, hidden, missing = (0, 0, 0)
        if self.tray:
            try:
                shown, hidden, missing = self.tray.get_counts()
            except Exception:                          # noqa: BLE001
                pass
        self.tray_counts = (shown, hidden, missing)

        stats = [("显示在任务栏", shown, ACCENT),
                 ("收在溢出区", hidden, "#C86A00" if hidden else FAINT),
                 ("暂无设置值", missing, "#C86A00" if missing else FAINT)]
        cw, gap = 166, 13
        for i, (label, num, color) in enumerate(stats):
            x = CX0 + i * (cw + gap)
            cv.create_polygon(round_rect(x, 120, x + cw, 200, 6), fill=CARD,
                              outline=CARD_LINE, width=1, tags="content")
            cv.create_text(x + 18, 152, text=str(num), anchor="w", fill=color,
                           font=f(26, "bold"), tags="content")
            cv.create_text(x + 18, 178, text=label, anchor="w", fill=MUTED,
                           font=f(11.5), tags="content")

        # 操作卡
        self.card(212, 96)
        self.label(CX0 + 14, 234, "批量设置所有托盘图标的显示状态", 12.5)
        self.label(CX0 + 14, 252, "会先结束 Explorer，写入注册表后再启动，避免退出时覆盖", 10.5, FAINT)
        self.widgets.append(Btn(cv, CX0 + 14, 268, 128, 30, "一键全部展开",
                                lambda: self.tray_apply(1), "primary", 4, 12))
        self.widgets.append(Btn(cv, CX0 + 152, 268, 128, 30, "一键恢复收纳",
                                lambda: self.tray_apply(0), "secondary", 4, 12))

        # 开关
        self.card(320, 56)
        self.label(CX0 + 14, 348, "执行后重启 Explorer 使设置生效", 12.5)
        self.sw_restart = Switch(cv, CX1 - 14 - 40, 338, self.restart_after,
                                 lambda v: setattr(self, "restart_after", v))
        self.widgets.append(self.sw_restart)

        # 日志
        self.card(388, 368, "运行日志")
        txt = tk.Text(cv, bg=LOG_BG, fg=LOG_FG, bd=0, highlightthickness=0,
                      font=(MONO, -12), padx=10, pady=8, wrap="word",
                      insertbackground=LOG_FG, spacing1=1)
        txt.tag_configure("ok", foreground="#7FD1A5")
        txt.tag_configure("warn", foreground="#F0C46B")
        txt.tag_configure("err", foreground="#F08C8C")
        txt.tag_configure("dim", foreground="#8A8A8A")
        cv.create_window(CX0 + 14, 414, window=txt, anchor="nw",
                         width=CX1 - CX0 - 28, height=326, tags="content")
        self.proxy.append(txt)
        self.log_box = txt
        for level, msg in self.log_history:            # 切页回来日志不丢
            self._insert_log(level, msg)
        if not self.log_history:
            self.log("面板已就绪 · " + time.strftime("%Y-%m-%d %H:%M"))

    def _insert_log(self, level, msg):
        box = self.log_box
        if box is None:
            return
        box.configure(state="normal")
        box.insert("end", "[%s] " % time.strftime("%H:%M:%S"), ("dim",))
        box.insert("end", msg + "\n", (level,))
        box.see("end")
        box.configure(state="disabled")

    def log(self, msg, level="info"):
        self.log_history.append((level, msg))
        if len(self.log_history) > 200:
            del self.log_history[:100]
        self._insert_log(level, msg)

    # ---------------------------------------------------------- 关于
    def _page_about(self):
        self.page_head("关于", "Windows 小工具集 · 绿色免安装 · 纯本地运行")
        cv = self.cv

        self.card(120, 132)
        cv.create_polygon(round_rect(CX0 + 16, 138, CX0 + 68, 190, 10),
                          fill=ACCENT, outline="", tags="content")
        cv.create_polygon(round_rect(CX0 + 28, 152, CX0 + 40, 164, 2), fill="#FFFFFF", outline="", tags="content")
        cv.create_polygon(round_rect(CX0 + 44, 152, CX0 + 56, 164, 2), fill="#FFFFFF", outline="", tags="content")
        cv.create_polygon(round_rect(CX0 + 28, 168, CX0 + 56, 174, 2), fill="#FFFFFF", outline="", tags="content")
        self.label(CX0 + 84, 156, "EasyPanel", 16, TEXT, "bold")
        self.label(CX0 + 84, 178, "版本 " + VERSION + " · MIT License", 11.5, MUTED)
        self.label(CX0 + 84, 198, "Python 3 + tkinter，无第三方依赖", 11.5, FAINT)
        self.label(CX0 + 84, 216, "github.com/Wayneway26/Easy_pannel", 11.5, ACCENT)
        self.widgets.append(Btn(cv, CX1 - 16 - 96, 138, 96, 30, "打开仓库",
                                self.open_repo, "secondary", 4, 12))

        self.card(264, 208, "包含的模块")
        rows = [("Dock", "屏幕底部悬浮启动栏，支持悬停放大、拖入应用",
                 "dock/EasyDock.pyw"),
                ("托盘图标", "批量展开 / 收纳 Windows 11 通知区域图标",
                 "TrayGUI.pyw"),
                ("紧急恢复", "桌面黑屏、任务栏消失时双击即可恢复 explorer",
                 "恢复桌面.bat")]
        for i, (name, desc, path) in enumerate(rows):
            y = 318 + i * 56
            self.label(CX0 + 14, y, name, 12.5, TEXT, "bold")
            self.label(CX0 + 14, y + 18, desc, 11, MUTED)
            self.label(CX1 - 14, y + 9, path, 10.5, FAINT, anchor="e")
            if i < len(rows) - 1:
                cv.create_line(CX0 + 14, y + 40, CX1 - 14, y + 40, fill="#EDEDED", tags="content")

        self.card(484, 200)
        self.label(CX0 + 14, 506, "特性", 12.5, TEXT, "bold")
        feats = ["只写 HKCU，不改系统级设置，随时可还原",
                 "不联网、不上报、不安装驱动",
                 "菜单入口与桌面快捷方式可一键卸载",
                 "配置保存在本地 config.json，可手工编辑",
                 "所有操作失败都有兜底，不会让桌面卡在黑屏"]
        for i, s in enumerate(feats):
            y = 534 + i * 28
            cv.create_oval(CX0 + 16, y - 3, CX0 + 22, y + 3, fill=ACCENT, outline="", tags="content")
            self.label(CX0 + 32, y, s, 11.5, TEXT)
        self.label(CX0 + 14, 712, "© 2026 Wayneway26", 10.5, FAINT)

    # ---------------------------------------------------------- 交互
    def _hit(self, x, y):
        for w in reversed(self.widgets):
            if w.hit(x, y):
                return w
        return None

    def _on_motion(self, ev):
        hit = self._hit(ev.x, ev.y)
        if hit is not self.hot:
            if self.hot is not None:
                self.hot.hover(False)
            if hit is not None:
                hit.hover(True)
            self.hot = hit
            self.cv.configure(cursor="hand2" if hit is not None else "")
        if ev.y < TITLE_H and hit is None:
            self.cv.configure(cursor="fleur" if self.drag else "")

    def _on_press(self, ev):
        hit = self._hit(ev.x, ev.y)
        if hit is not None:
            hit.click(ev.x, ev.y)
            return
        if ev.y < TITLE_H:
            self.drag = (ev.x_root - self.root.winfo_x(), ev.y_root - self.root.winfo_y())

    def _on_drag(self, ev):
        if self.drag:
            self.root.geometry("+%d+%d" % (ev.x_root - self.drag[0],
                                           ev.y_root - self.drag[1]))

    def _on_release(self, ev):
        self.drag = None

    def minimize(self):
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.after(120, self._restore_frame)

    def _restore_frame(self):
        if self.root.state() == "iconic":
            self.root.after(200, self._restore_frame)
        else:
            self.root.overrideredirect(True)

    def quit_app(self):
        self.root.destroy()

    def open_repo(self):
        try:
            os.startfile("https://github.com/Wayneway26/Easy_pannel")
        except Exception:                              # noqa: BLE001
            pass

    # ---------------------------------------------------------- Dock 控制
    def dock_pid(self):
        if not os.path.exists(DOCK_PID):
            return None
        try:
            with open(DOCK_PID) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            return None
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = c_void_p
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        code = c_ulong()
        k32.GetExitCodeProcess(c_void_p(h), byref(code))
        k32.CloseHandle(c_void_p(h))
        if code.value != 259:                          # 不是 STILL_ACTIVE
            try:
                os.remove(DOCK_PID)                    # 清掉进程崩溃后残留的 pid
            except OSError:
                pass
            return None
        return pid

    def start_dock(self):
        base = 0x00000008 | 0x00000200                 # DETACHED | NEW_PROCESS_GROUP
        for flags in (base | 0x01000000, base, 0):
            try:
                subprocess.Popen([pythonw(), DOCK_SCRIPT], cwd=DOCK_DIR, close_fds=True,
                                 creationflags=flags,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
        return False

    def stop_dock(self):
        pid = self.dock_pid()
        if pid:
            subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                           capture_output=True, timeout=10)
        try:
            os.remove(DOCK_PID)
        except OSError:
            pass

    def toggle_dock(self):
        if self.dock_pid():
            self.stop_dock()
            self.refresh_dock_state()
        else:
            if not self.start_dock():
                return
            self.root.after(600, self.refresh_dock_state)

    def restart_dock(self):
        if not self.dock_pid():
            self.toggle_dock()
            return
        self.stop_dock()
        self.root.after(350, lambda: (self.start_dock(), self.root.after(700, self.refresh_dock_state)))

    def _poll_dock_state(self):
        try:
            alive = self.dock_pid() is not None
            if getattr(self, "_alive_cache", None) != alive:
                self._alive_cache = alive
                if self.page == "dock":
                    self.refresh_dock_state()
        finally:
            self.root.after(1500, self._poll_dock_state)

    def refresh_dock_state(self):
        if self.page != "dock" or not hasattr(self, "btn_dock"):
            return
        alive = self.dock_pid() is not None
        self.btn_dock.set_text("关闭" if alive else "启动")
        self.btn_dock.set_kind("secondary" if alive else "primary")
        n = len(self.dock_cfg.get("items") or [])
        try:
            self.cv.itemconfig(
                self.status_label,
                text=("正在运行 · %d 个应用" % n) if alive
                else "未运行 · 点「启动」显示到桌面底部")
        except Exception:                              # noqa: BLE001
            pass

    def bump(self, key, delta):
        cfg = self.dock_cfg
        if key == "icon_size":
            val = int(cfg.get("icon_size", 48)) + int(delta)
            val = max(32, min(72, val))
            cfg["icon_size"] = val
            self.step_size.set_text("%d px" % val)
            # 图标变大时窗口高度要够，暂停区也跟着调
            cfg["padding"] = max(8, min(16, val // 6))
        else:
            val = float(cfg.get("zoom", 1.75)) + float(delta)
            val = max(1.25, min(2.0, round(val, 2)))
            cfg["zoom"] = val
            self.step_zoom.set_text("%.2f×" % val)
        self.save_dock_cfg()
        self.root.after(260, self.restart_dock)

    def set_theme(self, index):
        preset = {"dark": {"bg": "#1F1F24", "border": "#3A3A42"},
                  "light": {"bg": "#F7F7F9", "border": "#D8D8DE"}}["dark" if index == 0 else "light"]
        self.dock_cfg.update(preset)
        self.save_dock_cfg()
        self.root.after(200, self.restart_dock)

    def row_icon(self, item, x, y):
        """列表里 22px 的小图标；取不到就画个灰块"""
        cv = self.cv
        src = item.get("icon") or item.get("target") or ""
        img = self._icon_photo(src)
        if img is not None:
            return cv.create_image(x + 11, y + 11, image=img, tags="content")
        return cv.create_polygon(round_rect(x, y, x + 22, y + 22, 5),
                                 fill="#DCDCDC", outline="", tags="content")

    def _icon_photo(self, src):
        if not src or self.dock is None:
            return None
        if src in self.row_icons:
            return self.row_icons[src]
        img = None
        try:
            os.makedirs(ICON_CACHE, exist_ok=True)
            key = hashlib.md5(src.encode("utf-8")).hexdigest()[:16]
            png = os.path.join(ICON_CACHE, key + ".png")
            if not os.path.exists(png):
                ok = self.dock.extract_icon_png(src, png, 44, (251, 251, 251))
                if not ok:
                    png = None
            if png and os.path.exists(png):
                img = tk.PhotoImage(file=png).subsample(2)
        except Exception:                              # noqa: BLE001
            img = None
        self.row_icons[src] = img
        return img

    def add_item(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="选择要加入 Dock 的程序",
            filetypes=[("程序", "*.exe *.lnk *.bat *.cmd"), ("所有文件", "*.*")])
        if not path:
            return
        self.dock_cfg.setdefault("items", []).append(
            {"name": os.path.splitext(os.path.basename(path))[0], "target": path})
        self.save_dock_cfg()
        self.show_page("dock")
        self.root.after(200, self.restart_dock)

    def remove_item(self, index):
        items = self.dock_cfg.get("items") or []
        if 0 <= index < len(items):
            items.pop(index)
            self.save_dock_cfg()
            self.show_page("dock")
            self.root.after(200, self.restart_dock)

    def move_item(self, index, delta):
        items = self.dock_cfg.get("items") or []
        j = index + delta
        if 0 <= index < len(items) and 0 <= j < len(items):
            items[index], items[j] = items[j], items[index]
            self.save_dock_cfg()
            self.show_page("dock")
            self.root.after(200, self.restart_dock)

    # ---------------------------------------------------------- 托盘控制
    def tray_apply(self, value):
        if self.tray is None or self.busy:
            return
        self.busy = True
        want = "展开" if int(value) == 1 else "收纳"
        self.log("开始%s全部托盘图标…" % want)

        def job():
            t = self.tray
            try:
                t.spawn_watchdog(45)
                if self.restart_after:
                    self.log("正在结束 Explorer …")
                    if not t.kill_explorer():
                        self.log("Explorer 未能完全结束，修改可能被覆盖", "warn")
                n, errs = t.set_all_promoted(int(value))
                self.log("已写入 %d 个图标：IsPromoted=%d" % (n, int(value)), "ok")
                for e in errs[:3]:
                    self.log("写入失败 " + e, "err")
                if self.restart_after:
                    time.sleep(0.4)
                    ok = t.start_explorer()
                    self.log("Explorer 已启动" if ok else "Explorer 启动失败，请运行 恢复桌面.bat",
                             "ok" if ok else "err")
                    time.sleep(2.2)
                shown, hidden, missing = t.get_counts()
                self.log("校验结果：显示 %d / 收纳 %d / 无值 %d" % (shown, hidden, missing))
                self.root.after(0, self._after_tray)
            except Exception as e:                     # noqa: BLE001
                self.log("出错：%s" % e, "err")
                try:
                    t.start_explorer()
                except Exception:                      # noqa: BLE001
                    pass
                self.root.after(0, lambda: setattr(self, "busy", False))

        threading.Thread(target=job, daemon=True).start()

    def _after_tray(self):
        self.busy = False
        if self.page == "tray":
            self.show_page("tray")


def json_dump(obj, fh):
    json.dump(obj, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 截图自检
def run_shot(path=None):
    root = tk.Tk()
    panel = Panel(root)
    root.update()
    root.after(900, lambda: _grab(root, panel, path))
    root.mainloop()


def _grab(root, panel, path):
    root.update()
    x, y = root.winfo_x(), root.winfo_y()
    out = path or os.path.join(HERE, "_panel.png")
    try:
        if panel.dock:
            panel.dock.grab_png(x, y, W, H, out)
        print("saved:", out)
    except Exception as e:                             # noqa: BLE001
        print("grab failed:", e)
    root.destroy()


# ---------------------------------------------------------------- 入口
def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:                                  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                              # noqa: BLE001
            pass

    if "--shot" in sys.argv:
        idx = sys.argv.index("--shot")
        run_shot(sys.argv[idx + 1] if len(sys.argv) > idx + 1 else None)
        return

    root = tk.Tk()
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
