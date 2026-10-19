# -*- coding: utf-8 -*-
"""
EasyPanel —— Dock 与托盘图标的统一控制面板

视觉基准：Windows 11 / WinUI 3 的「设置」式布局（AutoDarkMode 那种感觉）
  · 窗口底 #F3F3F3，内容用白色卡片承载，圆角 8，1px 淡描边
  · 卡片内是「设置行」：左侧标题 + 灰描述，右侧控件，行间 1px 分割线
  · 强调色 #0067C0，正文微软雅黑
  · 左侧导航：选中项白底 + 左侧强调色圆角竖条

全部用 Canvas 自绘，零第三方依赖，保持「绿色单文件」的特点。

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
KEYMAP_SCRIPT = os.path.join(HERE, "KeyMap.pyw")
KEYMAP_CONFIG = os.path.join(HERE, "keymap.json")
KEYMAP_PID = os.path.join(HERE, "keymap.pid")

VERSION = "1.5.0"

# ---------------------------------------------------------------- 调色板
MAGIC = "#010203"            # 色键：窗口圆角外的部分靠它抠成真透明
BG = "#F3F3F3"               # 窗口底
CARD = "#FFFFFF"             # 卡片
CARD_LINE = "#EFEFEF"        # 卡片描边
HAIRLINE = "#EFEFEF"         # 行分割线
NAV_ACTIVE = "#FFFFFF"
ROW_HOVER = "#F5F5F5"
TEXT = "#1A1A1A"
MUTED = "#5D5D5D"
FAINT = "#8A8A8A"
ACCENT = "#0067C0"
ACCENT_HOVER = "#1975C2"
ACCENT_PRESS = "#005BA6"
BTN = "#FDFDFD"
BTN_LINE = "#D9D9D9"
BTN_HOVER = "#F5F5F5"
BTN_PRESS = "#EDEDED"
TRACK_OFF = "#9A9A9A"
DANGER = "#C42B1C"
SEG_BG = "#F0F0F0"
LOG_BG = "#FAFAFA"
LOG_FG = "#2B2B2B"

UI = "Segoe UI Variable Text"          # Win11 正文：西文原生，中文自动回退雅黑
DISPLAY = "Segoe UI Variable Display"  # Win11 标题
ICONS = "Segoe Fluent Icons"           # Win11 图标字形
MONO = "Consolas"

# ---------------------------------------------------------------- 布局
W, H = 860, 792
NAV_X, NAV_W = 10, 196       # 侧栏：10 .. 206
CX0, CX1 = 232, 844          # 内容区：232 .. 844（宽 612）
TITLE_H = 52


def f(size, weight="normal", family=None):
    """负数字号 = 像素，跨缩放看起来更稳。family 传 None 时动态取当前 UI 字体。"""
    return (family or UI, -int(size), weight)


def ensure_fonts():
    """Win10 上没有 Variable / Fluent Icons，回退到上一代，别显示成方块"""
    global UI, DISPLAY, ICONS
    try:
        import tkinter.font as tkfont
        fams = set(tkfont.families())
    except Exception:                                    # noqa: BLE001
        return
    if UI not in fams:
        UI = "Segoe UI" if "Segoe UI" in fams else "Microsoft YaHei UI"
    if DISPLAY not in fams:
        DISPLAY = UI
    if ICONS not in fams:
        ICONS = "Segoe MDL2 Assets"


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
    """标题栏上的最小化 / 关闭 —— tag 必须是 chrome，切页时不能被清掉"""

    def __init__(self, cv, x, y, kind, cmd):
        self.cv, self.cmd = cv, cmd
        self.kind = kind
        self.danger = kind == "close"
        self.box = (x, y - 4, x + 46, y + 40)
        if self.danger:
            self.bg = cv.create_polygon(tr_round(x, y - 4, x + 46, y + 40, 8),
                                        fill=BG, outline="", tags="chrome")
        else:
            self.bg = cv.create_rectangle(x, y - 4, x + 46, y + 40, fill=BG,
                                          outline="", tags="chrome")
        cx, cy = x + 23, y + 16
        if self.danger:
            self.marks = [cv.create_text(cx, cy, text="\ue8bb", fill=TEXT,
                                         font=(ICONS, -11), tags="chrome")]
        else:
            self.marks = [cv.create_text(cx, cy, text="\ue921", fill=TEXT,
                                         font=(ICONS, -11), tags="chrome")]

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
        self.x, self.y = x, y
        self.box = (x - 4, y - 8, x + w + 4, y + h + 8)
        self.track = cv.create_polygon(round_rect(x, y, x + w, y + h, h / 2.0),
                                       fill=ACCENT, outline="", tags="content")
        self.knob = cv.create_oval(0, 0, 0, 0, fill="#FFFFFF", outline="", tags="content")
        self._paint()

    def _paint(self):
        self.cv.itemconfig(self.track, fill=ACCENT if self.value else TRACK_OFF)
        r = (self.h - 8) / 2.0
        cy = self.y + self.h / 2.0
        cx = (self.x + self.w - self.h / 2.0) if self.value else (self.x + self.h / 2.0)
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
        cv.create_polygon(round_rect(x, y, x + w, y + h, 5),
                          fill=SEG_BG, outline="", tags="content")
        self.cells, self.labels = [], []
        seg_w = (w - 4) / float(len(self.options))
        for i, text in enumerate(self.options):
            x1 = x + 2 + i * seg_w
            cell = cv.create_polygon(round_rect(x1, y + 2, x1 + seg_w, y + h - 2, 4),
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
        cv.create_polygon(round_rect(x, y, x + w, y + h, 5),
                          fill="#FFFFFF", outline=BTN_LINE, width=1, tags="content")
        cy = y + h / 2.0
        self.zone = []
        for i, glyph in enumerate(("-", "+")):
            zx = x + (4 if i == 0 else w - 32)
            z = cv.create_polygon(round_rect(zx, y + 3, zx + 28, y + h - 3, 4),
                                  fill="", outline="", tags="content")
            cv.create_line(zx + 9, cy, zx + 19, cy, fill=TEXT, width=1.1, tags="content")
            if glyph == "+":
                cv.create_line(zx + 14, cy - 5, zx + 14, cy + 5, fill=TEXT, width=1.1, tags="content")
            self.zone.append(z)
        self.label = cv.create_text(x + w / 2.0, cy, text=text, fill=TEXT,
                                    font=f(13), tags="content")

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
    """左侧导航项（tag=ui，与页面内容分离，永不被清理）"""

    def __init__(self, cv, x, y, w, h, key, label, icon, cmd):
        self.cv, self.key, self.cmd = cv, key, cmd
        self.box = (x, y, x + w, y + h)
        self.bg = cv.create_polygon(round_rect(x, y, x + w, y + h, 6),
                                    fill="", outline="", tags="ui")
        self.bar = cv.create_polygon(round_rect(x + 3, y + h / 2.0 - 8, x + 6, y + h / 2.0 + 8, 1.5),
                                     fill="", outline="", tags="ui")
        self.icon = nav_icon(cv, x + 28, y + h / 2.0, icon, MUTED)
        self.label = cv.create_text(x + 48, y + h / 2.0, text=label, anchor="w",
                                    fill=TEXT, font=f(13.5), tags="ui")
        self.active = False

    def set_active(self, on):
        self.active = on
        self.cv.itemconfig(self.bg, fill=NAV_ACTIVE if on else "")
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
    """Dock 应用列表里的一行 —— 内部按钮要在 click 里转发命中"""

    def __init__(self, cv, x, y, w, h, panel, index):
        self.cv, self.panel, self.index = cv, panel, index
        self.box = (x, y, x + w, y + h)
        self.bg = cv.create_polygon(round_rect(x, y, x + w, y + h, 5),
                                    fill="", outline="", tags="content")
        item = panel.dock_cfg["items"][index]
        self.thumb = panel.row_icon(item, x + 8, y + (h - 22) / 2.0)
        cv.create_text(x + 42, y + h / 2.0, text=item.get("name") or "未命名",
                       anchor="w", fill=TEXT, font=f(12.5), tags="content")
        self.btns = []
        for i, (glyph, cb, title) in enumerate((
                ("up", lambda: panel.move_item(index, -1), "上移"),
                ("down", lambda: panel.move_item(index, +1), "下移"),
                ("x", lambda: panel.remove_item(index), "移除"))):
            self.btns.append(SmallIcon(cv, x + w - 84 + i * 28, y + (h - 24) / 2.0,
                                       24, glyph, cb))

    def hover(self, on):
        self.cv.itemconfig(self.bg, fill=ROW_HOVER if on else "")

    def click(self, x, y):
        for b in self.btns:                 # 关键修复：把点击转发给行内小按钮
            if b.hit(x, y):
                b.click(x, y)
                return


class SmallIcon(Widget):
    """行内的小图标按钮：↑ ↓ 🗑"""

    def __init__(self, cv, x, y, size, glyph, cmd):
        self.cv, self.cmd = cv, cmd
        self.box = (x, y, x + size, y + size)
        self.bg = cv.create_polygon(round_rect(x, y, x + size, y + size, 4),
                                    fill="", outline="", tags="content")
        cx, cy = x + size / 2.0, y + size / 2.0
        col = MUTED if glyph != "x" else FAINT
        ch = {"up": "\ue74a", "down": "\ue74b", "x": "\ue74d"}.get(glyph, "\ue711")
        self.mark = cv.create_text(cx, cy, text=ch, fill=col,
                                   font=(ICONS, -11), tags="content")

    def hover(self, on):
        self.cv.itemconfig(self.bg, fill="#E8E8E8" if on else "")

    def click(self, x, y):
        self.cmd()


def nav_icon(cv, cx, cy, kind, color):
    """导航图标用 Segoe Fluent Icons 字形，和 Win11 设置页同一套图标。
    返回值保持 (实心图形, 描边图形) 的结构，方便上层统一改色。"""
    glyph = {"dock": "\ue71d", "tray": "\ue8fd", "info": "\ue946",
             "key": "\ue765", "taskbar": "\ue7f4"}.get(kind, "\ue946")
    item = cv.create_text(cx, cy, text=glyph, fill=color,
                          font=(ICONS, -16), tags="ui")
    return [item], []


# ---------------------------------------------------------------- 主面板
class Panel:
    def __init__(self, root):
        self.root = root
        ensure_fonts()
        self.dock = load_module(DOCK_SCRIPT, "easy_dock")
        self.tray = load_module(TRAY_SCRIPT, "tray_core")
        self.dock_cfg = self._read_dock_cfg()
        self.keymap_cfg = self._read_keymap_cfg()
        self.page = "dock"
        self.widgets = []                 # 页面控件（切页清空）
        self.chrome_widgets = []          # 标题栏 + 导航（永不清空）
        self.proxy = []

        self.hot = None
        self.drag = None
        self.busy = False
        self._quit_pending = False
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

    def _read_keymap_cfg(self):
        try:
            with open(KEYMAP_CONFIG, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
        cfg.setdefault("enabled", False)
        cfg.setdefault("rules", [])
        return cfg

    def save_keymap_cfg(self):
        try:
            with open(KEYMAP_CONFIG, "w", encoding="utf-8") as fh:
                json.dump(self.keymap_cfg, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

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
        self.wx, self.wy = (sw - W) // 2, max(16, (sh - H) // 2 - 24)
        r.geometry("%dx%d+%d+%d" % (W, H, self.wx, self.wy))
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

        # 关不掉的三重保险：Alt+F4 / Esc / 系统关闭消息
        r.bind("<Alt-F4>", lambda e: self.quit_app())
        r.bind("<Escape>", lambda e: self.quit_app())
        r.protocol("WM_DELETE_WINDOW", self.quit_app)

    # -- 外壳 ------------------------------------------------------
    def _paint_backdrop(self):
        """Seelen 风格：窗口底做成磨砂玻璃（桌面模糊 + 浅色蒙层），卡片浮在上面"""
        try:
            import backdrop
            png = os.path.join(HERE, "cache", "panel-glass.png")
            os.makedirs(os.path.dirname(png), exist_ok=True)
            backdrop.make(png, self.wx, self.wy, W, H, radius=10,
                          tint=(250, 250, 254), opacity=0.78, scale=6, blur=4, passes=4)
            self.bg_img = tk.PhotoImage(file=png)
            self.cv.create_image(0, 0, image=self.bg_img, anchor="nw", tags="chrome")
            return True
        except Exception:                              # noqa: BLE001
            return False

    def _draw_chrome(self):
        cv = self.cv
        if not self._paint_backdrop():
            cv.create_polygon(round_rect(0, 0, W, H, 10), fill=BG, outline="",
                              tags="chrome")
        cv.create_polygon(round_rect(0, 0, W, H, 10), fill="", outline="#D2D2D2",
                          width=1, tags="chrome")

        # 应用图标：三块错落的「面板」
        cv.create_polygon(round_rect(20, 16, 40, 36, 5), fill=ACCENT, outline="", tags="chrome")
        cv.create_polygon(round_rect(25, 21, 31, 27, 1.5), fill="#FFFFFF", outline="", tags="chrome")
        cv.create_polygon(round_rect(25, 30, 31, 34, 1.2), fill="#FFFFFF", outline="", tags="chrome")
        cv.create_polygon(round_rect(33, 21, 35, 34, 1.0), fill="#FFFFFF", outline="", tags="chrome")
        cv.create_text(50, 26, text="EasyPanel", anchor="w", fill=TEXT,
                       font=f(13.5, "bold", DISPLAY), tags="chrome")
        cv.create_text(W - 116, 26, text="v" + VERSION, anchor="e", fill=FAINT,
                       font=f(10.5), tags="chrome")

        self.chrome_widgets.append(TitleBtn(cv, W - 92, 8, "min", self.minimize))
        self.chrome_widgets.append(TitleBtn(cv, W - 46, 8, "close", self.quit_app))

        # 左侧导航
        self.nav = {}
        y = 58
        for key, label, icon in (("dock", "Dock", "dock"),
                                 ("tray", "托盘图标", "tray"),
                                 ("keymap", "快捷键", "key"),
                                 ("taskbar", "任务栏", "taskbar"),
                                 ("about", "关于", "info")):
            item = NavItem(cv, NAV_X, y, NAV_W, 42, key, label, icon, self.show_page)
            self.nav[key] = item
            self.chrome_widgets.append(item)
            y += 44

        cv.create_line(NAV_X + 12, 58 + 5 * 44, NAV_X + NAV_W - 12,
                       58 + 5 * 44, fill="#E4E4E4", tags="ui")

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
        self.widgets = []                              # 页面控件整体丢弃，chrome 不受影响
        self.cv.delete("content")
        self.hot = None
        for k, item in self.nav.items():
            item.set_active(k == key)
        {"dock": self._page_dock, "tray": self._page_tray,
         "keymap": self._page_keymap, "taskbar": self._page_taskbar,
         "about": self._page_about}[key]()

    # -- 通用绘制小工具 --------------------------------------------
    def card(self, y, h, title=None, pad=18):
        """一张设置卡片。title 按 WinUI 规范画在卡片**外面**上方。"""
        cv = self.cv
        cv.create_polygon(round_rect(CX0, y, CX1, y + h, 10), fill=CARD,
                          outline=CARD_LINE, width=1, tags="content")
        if title:
            cv.create_text(CX0 + 2, y - 16, text=title, anchor="w", fill=TEXT,
                           font=f(13.5, "bold", DISPLAY), tags="content")
        return y

    def label(self, x, y, text, size=13.5, color=TEXT, weight="normal", anchor="w"):
        return self.cv.create_text(x, y, text=text, anchor=anchor, fill=color,
                                   font=f(size, weight), tags="content")

    def page_head(self, title, subtitle):
        self.cv.create_text(CX0 - 4, 78, text=title, anchor="w", fill=TEXT,
                            font=f(26, "bold", DISPLAY), tags="content")
        self.label(CX0 - 4, 108, subtitle, 12, MUTED)

    def row_text(self, y, title, desc=None):
        """设置行左侧的两行文字（行高 60）"""
        self.label(CX0 + 18, y + 24, title, 13.5)
        if desc:
            self.label(CX0 + 18, y + 43, desc, 12, MUTED)

    def hairline(self, y):
        self.cv.create_line(CX0 + 18, y, CX1 - 18, y, fill=HAIRLINE, tags="content")

    # ---------------------------------------------------------- Dock
    def _page_dock(self):
        cfg = self.dock_cfg
        self.page_head("Dock", "屏幕底部的应用启动栏 · 悬停放大 · 单击启动")
        cv = self.cv

        # --- 状态卡 ---
        self.card(126, 80)
        cv.create_polygon(round_rect(CX0 + 18, 144, CX0 + 62, 188, 11),
                          fill="#2B2B31", outline="", tags="content")
        cv.create_text(CX0 + 40, 166, text="\ue71d", fill="#FFFFFF",
                       font=(ICONS, -20), tags="content")
        alive = self.dock_pid() is not None
        n = len(cfg.get("items") or [])
        self.label(CX0 + 78, 156, "Easy Dock", 14, TEXT, "bold")
        self.status_label = self.label(
            CX0 + 78, 178,
            ("正在运行 · %d 个应用" % n) if alive else "未运行 · 点「启动」显示到桌面底部",
            11.5, MUTED)

        self.btn_dock = Btn(cv, CX1 - 18 - 100, 148, 100, 32,
                            "关闭" if alive else "启动",
                            self.toggle_dock, "secondary" if alive else "primary")
        self.widgets.append(self.btn_dock)

        # --- 外观卡 ---
        self.card(244, 180, "外观")
        rows = 244
        self.row_text(rows, "图标大小", "每个图标的边长，40–72 px")
        self.step_size = Stepper(cv, CX1 - 138, rows + 14, 120, 32,
                                 "%d px" % cfg["icon_size"],
                                 lambda: self.bump("icon_size", -4),
                                 lambda: self.bump("icon_size", 4))
        self.widgets.append(self.step_size)
        self.hairline(rows + 60)

        rows += 60
        self.row_text(rows, "悬停放大", "鼠标移上去时放大的倍数")
        self.step_zoom = Stepper(cv, CX1 - 138, rows + 14, 120, 32,
                                 "%.2f×" % float(cfg.get("zoom", 1.75)),
                                 lambda: self.bump("zoom", -0.25),
                                 lambda: self.bump("zoom", 0.25))
        self.widgets.append(self.step_zoom)
        self.hairline(rows + 60)

        rows += 60
        self.row_text(rows, "Dock 主题", "深色更接近 iOS 原生气质")
        theme = "dark" if str(cfg.get("bg", "")).upper() in ("#1F1F24", "#1F1F1F") else "light"
        self.seg_theme = Seg(cv, CX1 - 146, rows + 14, 128, 32, ["深色", "浅色"],
                             0 if theme == "dark" else 1, self.set_theme)
        self.widgets.append(self.seg_theme)

        # --- 应用列表 ---
        self.card(462, 220, "Dock 中的应用")
        items = cfg.get("items") or []
        self.label(CX1 - 18, 446,
                   ("%d 个 · 显示前 6 个" % len(items)) if len(items) > 6
                   else "%d 个" % len(items), 11, FAINT, anchor="e")

        max_rows = 6
        top = 470
        row_h = 34
        if not items:
            self.label(CX0 + 18, 500, "还没有添加任何应用", 12.5, MUTED)
            self.label(CX0 + 18, 522, "点下面的「添加应用…」把常用的程序放进来", 12, MUTED)
        for i, _item in enumerate(items[:max_rows]):
            self.widgets.append(AppRow(cv, CX0 + 10, top + i * row_h, CX1 - CX0 - 20,
                                       row_h, self, i))
            if i < min(len(items), max_rows) - 1:
                self.hairline(top + (i + 1) * row_h - 4)

        self.widgets.append(Btn(cv, CX0, 700, 116, 32, "添加应用…",
                                self.add_item, "secondary", 4, 12.5))
        self.widgets.append(Btn(cv, CX0 + 128, 700, 108, 32, "重新启动",
                                self.restart_dock, "secondary", 4, 12.5))

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
        cw, gap = 196, 12
        for i, (label, num, color) in enumerate(stats):
            x = CX0 + i * (cw + gap)
            cv.create_polygon(round_rect(x, 126, x + cw, 202, 8), fill=CARD,
                              outline=CARD_LINE, width=1, tags="content")
            cv.create_text(x + 18, 158, text=str(num), anchor="w", fill=color,
                           font=f(26, "bold"), tags="content")
            cv.create_text(x + 18, 184, text=label, anchor="w", fill=MUTED,
                           font=f(11.5), tags="content")

        # 操作卡
        self.card(240, 92, "批量操作")
        self.label(CX0 + 18, 264, "批量设置所有托盘图标的显示状态", 13.5)
        self.label(CX0 + 18, 283, "会先结束 Explorer，写入注册表后再启动，避免退出时覆盖", 12, MUTED)
        self.widgets.append(Btn(cv, CX0 + 18, 294, 132, 30, "一键全部展开",
                                lambda: self.tray_apply(1), "primary", 4, 12.5))
        self.widgets.append(Btn(cv, CX0 + 160, 294, 132, 30, "一键恢复收纳",
                                lambda: self.tray_apply(0), "secondary", 4, 12.5))

        # 开关
        self.card(370, 60, "行为")
        self.label(CX0 + 18, 394, "执行后重启 Explorer 使设置生效", 13.5)
        self.label(CX0 + 18, 413, "关闭时只写注册表，需要手动重启资源管理器", 12, MUTED)
        self.sw_restart = Switch(cv, CX1 - 18 - 40, 390, self.restart_after,
                                 lambda v: setattr(self, "restart_after", v))
        self.widgets.append(self.sw_restart)

        # 日志
        self.card(468, 288, "运行日志")
        txt = tk.Text(cv, bg=LOG_BG, fg=LOG_FG, bd=0, highlightthickness=0,
                      font=(MONO, -12), padx=12, pady=10, wrap="word",
                      insertbackground=LOG_FG, spacing1=1, spacing3=1)
        txt.tag_configure("ok", foreground="#0F7B0F")
        txt.tag_configure("warn", foreground="#9D5D00")
        txt.tag_configure("err", foreground="#C42B1C")
        txt.tag_configure("dim", foreground="#8A8A8A")
        cv.create_window(CX0 + 18, 480, window=txt, anchor="nw",
                         width=CX1 - CX0 - 36, height=264, tags="content")
        self.proxy.append(txt)
        self.log_box = txt
        for level, msg in self.log_history:            # 切页回来日志不丢
            self._insert_log(level, msg)
        if not self.log_history:
            self.log("面板已就绪 · " + time.strftime("%Y-%m-%d %H:%M"))

    # ---------------------------------------------------------- 快捷键
    def _page_keymap(self):
        self.page_head("快捷键", "全局按键重映射 · 按下左边，系统收到右边")
        cv = self.cv
        rules = self.keymap_cfg.get("rules") or []
        alive = self.keymap_pid() is not None

        # 状态卡
        self.card(126, 80)
        cv.create_polygon(round_rect(CX0 + 18, 144, CX0 + 62, 188, 11),
                          fill="#2B2B31", outline="", tags="content")
        cv.create_text(CX0 + 40, 166, text="\ue765", fill="#FFFFFF",
                       font=(ICONS, -20), tags="content")
        self.label(CX0 + 78, 156, "按键重映射引擎", 14, TEXT, "bold")
        self.label(CX0 + 78, 178,
                   ("已启用 · %d 条规则" % len(rules)) if alive
                   else "未启用 · 点「启用」后立即生效", 11.5, MUTED)
        self.btn_km = Btn(cv, CX1 - 18 - 100, 148, 100, 32,
                          "停止" if alive else "启用", self.toggle_keymap,
                          "secondary" if alive else "primary")
        self.widgets.append(self.btn_km)

        # 规则列表
        top = 244
        rows = rules[:6]
        h = max(1, len(rows)) * 46 + 20
        self.card(top, h, "映射规则")
        if not rows:
            self.label(CX0 + 18, top + 30, "还没有规则", 12.5, MUTED)
            self.label(CX0 + 18, top + 52, "在下面填一个，比如 ctrl+c → alt+c", 11.5, FAINT)
        for i, rule in enumerate(rows):
            y = top + 8 + i * 46
            self.label(CX0 + 18, y + 23, str(rule.get("from", "")), 13.5, TEXT)
            self.label(CX0 + 168, y + 23, "→", 14, FAINT)
            self.label(CX0 + 200, y + 23, str(rule.get("to", "")), 13.5, ACCENT)
            self.widgets.append(Btn(cv, CX1 - 18 - 56, y + 9, 56, 28, "删除",
                                    lambda i=i: self.del_rule(i), "secondary", 4, 11.5))
            if i < len(rows) - 1:
                self.hairline(y + 46)

        # 新增一行
        ay = top + h + 30
        self.label(CX0, ay + 9, "新增", 12, FAINT)
        e1 = tk.Entry(cv, bd=0, highlightthickness=1, highlightbackground=BTN_LINE,
                      highlightcolor=ACCENT, bg="#FFFFFF", fg=TEXT,
                      font=(UI, -13), insertbackground=TEXT)
        e1.insert(0, "ctrl+c")
        cv.create_window(CX0 + 52, ay, window=e1, anchor="nw", width=148, height=32,
                         tags="content")
        self.proxy.append(e1)
        self.e_from = e1

        self.label(CX0 + 212, ay + 16, "→", 14, FAINT)
        e2 = tk.Entry(cv, bd=0, highlightthickness=1, highlightbackground=BTN_LINE,
                      highlightcolor=ACCENT, bg="#FFFFFF", fg=TEXT,
                      font=(UI, -13), insertbackground=TEXT)
        e2.insert(0, "alt+c")
        cv.create_window(CX0 + 242, ay, window=e2, anchor="nw", width=148, height=32,
                         tags="content")
        self.proxy.append(e2)
        self.e_to = e2

        self.widgets.append(Btn(cv, CX0 + 410, ay, 76, 32, "添加",
                                self.add_rule, "primary", 4, 12.5))

        self.label(CX0, ay + 58, "支持 ctrl / alt / shift / win 与字母、数字、F1–F12、方向键；组合要写全，如 ctrl+shift+c。",
                   11, FAINT)
        self.label(CX0, ay + 78, "规则对全局生效，会覆盖系统和其他软件的默认快捷键；只匹配修饰键完全一致的组合。",
                   11, FAINT)

    # ---------------------------------------------------------- 任务栏
    def _page_taskbar(self):
        self.page_head("任务栏", "隐藏 Windows 底部任务栏 · 由 Dock 接管")
        cv = self.cv
        visible = self.taskbar_visible()

        self.card(126, 80)
        cv.create_polygon(round_rect(CX0 + 18, 144, CX0 + 62, 188, 11),
                          fill="#2B2B31", outline="", tags="content")
        cv.create_text(CX0 + 40, 166, text="\ue7f4", fill="#FFFFFF",
                       font=(ICONS, -20), tags="content")
        self.label(CX0 + 78, 156, "Windows 任务栏", 14, TEXT, "bold")
        self.label(CX0 + 78, 178,
                   "当前显示在屏幕底部" if visible else "已隐藏 · 底部交给 Dock",
                   11.5, MUTED)
        self.btn_tb = Btn(cv, CX1 - 18 - 100, 148, 100, 32,
                          "隐藏" if visible else "恢复", self.toggle_taskbar,
                          "secondary" if visible else "primary")
        self.widgets.append(self.btn_tb)

        self.card(234, 136, "说明")
        tips = ["隐藏的是任务栏窗口本身（Shell_TrayWnd），explorer.exe 和桌面图标都不受影响。",
                "工作区不会自动扩展，Dock 仍然贴在原来的任务栏上方。",
                "接了多显示器时，副屏任务栏会一起隐藏 / 恢复。",
                "Dock 没在运行时隐藏任务栏，底部会空一块——先启动 Dock 再隐藏。"]
        for i, s in enumerate(tips):
            y = 262 + i * 30
            cv.create_oval(CX0 + 19, y - 3, CX0 + 24, y + 2, fill=ACCENT,
                           outline="", tags="content")
            self.label(CX0 + 34, y, s, 11.5, TEXT)

        self.card(400, 116, "万一面板也被关掉")
        self.label(CX0 + 18, 432, "双击仓库里的「恢复桌面.bat」，它会重启资源管理器并显示任务栏。",
                   12, TEXT)
        self.label(CX0 + 18, 456, "也可以按 Ctrl+Shift+Esc 打开任务管理器 → 运行新任务 → explorer.exe。",
                   12, TEXT)
        self.label(CX0 + 18, 488, "隐藏是不写注册表的临时状态，重启电脑会自动恢复。", 11, FAINT)

    # ---------------------------------------------------------- 关于
    def _page_about(self):
        self.page_head("关于", "Windows 小工具集 · 绿色免安装 · 纯本地运行")
        cv = self.cv

        self.card(126, 124)
        cv.create_polygon(round_rect(CX0 + 18, 144, CX0 + 74, 200, 12),
                          fill=ACCENT, outline="", tags="content")
        cv.create_text(CX0 + 47, 172, text="\ue71d", fill="#FFFFFF",
                       font=(ICONS, -30), tags="content")
        self.cv.create_text(CX0 + 90, 156, text="EasyPanel", anchor="w", fill=TEXT,
                            font=f(19, "bold", DISPLAY), tags="content")
        self.label(CX0 + 90, 180, "版本 " + VERSION + " · MIT License", 11.5, MUTED)
        self.label(CX0 + 90, 200, "Python 3 + tkinter，无第三方依赖", 12, MUTED)
        self.label(CX0 + 90, 220, "github.com/Wayneway26/Easy_pannel", 11.5, ACCENT)
        self.widgets.append(Btn(cv, CX1 - 18 - 108, 142, 108, 32, "打开仓库",
                                self.open_repo, "secondary", 4, 12.5))

        self.card(288, 164, "包含的模块")
        rows = [("Dock", "屏幕底部悬浮启动栏，支持悬停放大、拖入应用", "dock/EasyDock.pyw"),
                ("托盘图标", "批量展开 / 收纳 Windows 11 通知区域图标", "TrayGUI.pyw"),
                ("紧急恢复", "桌面黑屏、任务栏消失时双击即可恢复 explorer", "恢复桌面.bat")]
        for i, (name, desc, path) in enumerate(rows):
            y = 312 + i * 52
            self.label(CX0 + 18, y, name, 13, TEXT, "bold")
            self.label(CX0 + 18, y + 19, desc, 12, MUTED)
            self.label(CX1 - 18, y + 9, path, 10.5, FAINT, anchor="e")
            if i < len(rows) - 1:
                self.hairline(y + 42)

        self.card(490, 166, "特性")
        feats = ["只写 HKCU，不改系统级设置，随时可还原",
                 "不联网、不上报、不安装驱动",
                 "菜单入口与桌面快捷方式可一键卸载",
                 "配置保存在本地 config.json，可手工编辑",
                 "所有操作失败都有兜底，不会让桌面卡在黑屏"]
        for i, s in enumerate(feats):
            y = 518 + i * 30
            cv.create_oval(CX0 + 19, y - 3, CX0 + 25, y + 3, fill=ACCENT, outline="",
                           tags="content")
            self.label(CX0 + 36, y, s, 12, TEXT)
        self.label(CX0 + 18, 704, "© 2026 Wayneway26", 10.5, FAINT)

    # ---------------------------------------------------------- 交互
    def _hit(self, x, y):
        for w in reversed(self.chrome_widgets):        # 标题栏 / 导航优先，永远不会丢
            if w.hit(x, y):
                return w
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
        """关闭。托盘操作进行中时先等它跑完，避免 Explorer 停在半路。"""
        if self.busy:
            self._quit_pending = True
            self.log("正在执行托盘操作，完成后自动退出…", "warn")
            return
        self._force_quit()

    def _force_quit(self):
        try:
            self.root.destroy()
        except Exception:                              # noqa: BLE001
            pass
        os._exit(0)                                    # 保证进程一定退干净

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

    def _kill_dock_fallback(self):
        """pid 文件丢失 / 不匹配时的兜底：按命令行找 EasyDock.pyw 的进程"""
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='pythonw.exe' or name='python.exe'",
                 "get", "ProcessId,CommandLine", "/format:list"],
                capture_output=True, text=True, timeout=8,
                creationflags=0x08000000).stdout
        except Exception:                              # noqa: BLE001
            return
        cur = os.path.basename(DOCK_SCRIPT).lower()
        hit = False
        for line in out.splitlines():
            line = line.strip()
            if line.lower().startswith("commandline="):
                hit = cur in line.lower()
            elif line.lower().startswith("processid=") and hit:
                pid = line.split("=", 1)[1].strip()
                if pid.isdigit():
                    subprocess.run(["taskkill", "/f", "/pid", pid],
                                   capture_output=True, timeout=8)
                hit = False

    def stop_dock(self):
        pid = self.dock_pid()
        if pid:
            subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                           capture_output=True, timeout=10)
        else:
            self._kill_dock_fallback()
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
        self.root.after(350, lambda: (self.start_dock(),
                                      self.root.after(700, self.refresh_dock_state)))

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

    # ---------------------------------------------------------- 按键映射
    def _pid_alive(self, path):
        """读 pid 文件并确认进程还活着"""
        if not os.path.exists(path):
            return None
        try:
            with open(path) as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            return None
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = c_void_p
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        code = c_ulong()
        k32.GetExitCodeProcess(c_void_p(h), byref(code))
        k32.CloseHandle(c_void_p(h))
        if code.value != 259:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        return pid

    def keymap_pid(self):
        return self._pid_alive(KEYMAP_PID)

    def start_keymap(self):
        self.save_keymap_cfg()
        base = 0x00000008 | 0x00000200                 # DETACHED | NEW_PROCESS_GROUP
        for flags in (base | 0x01000000, base, 0):
            try:
                subprocess.Popen([pythonw(), KEYMAP_SCRIPT], cwd=HERE, close_fds=True,
                                 creationflags=flags,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
        return False

    def stop_keymap(self):
        pid = self.keymap_pid()
        if pid:
            subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                           capture_output=True, timeout=10)
        try:
            os.remove(KEYMAP_PID)
        except OSError:
            pass

    def toggle_keymap(self):
        alive = self.keymap_pid() is not None
        self.keymap_cfg["enabled"] = not alive
        if alive:
            self.stop_keymap()
        else:
            if not self.start_keymap():
                self.keymap_cfg["enabled"] = False
                self.save_keymap_cfg()
                return
        self.root.after(450, lambda: self.show_page("keymap"))

    def add_rule(self):
        def val(attr):
            w = getattr(self, attr, None)
            try:
                return w.get().strip().lower() if w is not None else ""
            except Exception:                          # noqa: BLE001
                return ""
        a, b = val("e_from"), val("e_to")
        if not a or not b:
            return
        rules = self.keymap_cfg.setdefault("rules", [])
        if len(rules) >= 12:
            return
        rules.append({"from": a, "to": b})
        self.save_keymap_cfg()
        if self.keymap_pid():
            self.stop_keymap()
            self.root.after(220, self.start_keymap)
        self.root.after(420, lambda: self.show_page("keymap"))

    def del_rule(self, i):
        rules = self.keymap_cfg.get("rules") or []
        if 0 <= i < len(rules):
            rules.pop(i)
            self.save_keymap_cfg()
            if self.keymap_pid():
                self.stop_keymap()
                self.root.after(220, self.start_keymap)
            self.root.after(420, lambda: self.show_page("keymap"))

    # ---------------------------------------------------------- 任务栏
    def _taskbar_hwnd(self):
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = c_void_p
        u32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        u32.FindWindowExW.restype = c_void_p
        u32.FindWindowExW.argtypes = [c_void_p, c_void_p, wt.LPCWSTR, wt.LPCWSTR]
        handles = []
        h = u32.FindWindowW("Shell_TrayWnd", None)
        if h:
            handles.append(h)
        h2 = u32.FindWindowW("Shell_SecondaryTrayWnd", None)
        guard = 0
        while h2 and guard < 8:                        # 多显示器
            handles.append(h2)
            h2 = u32.FindWindowExW(None, c_void_p(h2), "Shell_SecondaryTrayWnd", None)
            guard += 1
        return handles

    def taskbar_visible(self):
        u32 = ctypes.windll.user32
        for h in self._taskbar_hwnd():
            return bool(u32.IsWindowVisible(c_void_p(h)))
        return False

    def set_taskbar(self, visible):
        u32 = ctypes.windll.user32
        u32.ShowWindow.argtypes = [c_void_p, ctypes.c_int]
        u32.ShowWindow.restype = wt.BOOL
        for h in self._taskbar_hwnd():
            u32.ShowWindow(c_void_p(h), 5 if visible else 0)   # SW_SHOW / SW_HIDE

    def toggle_taskbar(self):
        self.set_taskbar(not self.taskbar_visible())
        self.root.after(150, lambda: self.show_page("taskbar"))

    # ---------------------------------------------------------- 设置
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
        return cv.create_polygon(round_rect(x, y, x + 22, y + 22, 6),
                                 fill="#E4E4E4", outline="", tags="content")

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
                ok = self.dock.extract_icon_png(src, png, 44, (255, 255, 255))
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

    def _after_tray(self):
        self.busy = False
        if self._quit_pending:                         # 用户点了关闭，等操作结束再退
            self._force_quit()
            return
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
        print("saved:", out, os.path.getsize(out))
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
