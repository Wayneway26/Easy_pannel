# -*- coding: utf-8 -*-
"""
Easy Dock —— iOS 风格底部 Dock（Windows 10/11）

设计目标
--------
1. 屏幕底部中央一条悬浮面板，圆角、无边框、始终置顶；
2. 鼠标移上去图标放大、相邻图标被轻轻推开（macOS / iOS 的放大手感）；
3. 点一下启动程序，右键管理（添加 / 移除 / 重新居中 / 退出）；
4. 纯标准库实现（tkinter + ctypes），不需要安装任何第三方包。

技术要点
--------
- 窗体：overrideredirect 去掉边框和任务栏按钮，-transparentcolor 做色键透明，
  于是圆角外的区域真正透明（不是刷一块底色盖住桌面）。
- 图标：解析 .lnk 拿到真实 exe，再用 PrivateExtractIconsW 提取 128px 大图标，
  转成 PNG 缓存；写盘时把 alpha 压平到面板底色，避免 Tk 不支持半透明导致黑边。
- 放大：图标以底边对齐，缩放时按「当前宽度」重新排布，所以相邻图标会被自然推开。
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import struct
import sys
import zlib
from ctypes import byref, sizeof, c_void_p, c_int

import tkinter as tk
from tkinter import filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
ICON_DIR = os.path.join(HERE, "icons")
LOG_PATH = os.path.join(HERE, "dock.log")

MAGIC = "#010203"          # 色键：这个颜色会被系统整块抠掉
# 这些倍数的 scale/2 都能写成小分母分数，zoom+subsample 不丢精度
SCALES = (1.0, 1.25, 1.375, 1.5, 1.75, 2.0)

u32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

# ---- 64 位下必须声明返回值与参数类型，否则句柄被截断成 int ----
gdi.CreateDIBSection.restype = c_void_p
gdi.CreateDIBSection.argtypes = [c_void_p, c_void_p, ctypes.c_uint,
                                 ctypes.POINTER(c_void_p), c_void_p, ctypes.c_uint]
gdi.CreateCompatibleDC.restype = c_void_p
gdi.CreateCompatibleDC.argtypes = [c_void_p]
gdi.CreateCompatibleBitmap.restype = c_void_p
gdi.CreateCompatibleBitmap.argtypes = [c_void_p, c_int, c_int]
gdi.CreateSolidBrush.restype = c_void_p
gdi.CreateSolidBrush.argtypes = [wt.DWORD]
gdi.CreatePen.restype = c_void_p
gdi.CreatePen.argtypes = [c_int, c_int, wt.DWORD]
gdi.Rectangle.argtypes = [c_void_p, c_int, c_int, c_int, c_int]
gdi.BitBlt.argtypes = [c_void_p, c_int, c_int, c_int, c_int, c_void_p,
                       c_int, c_int, ctypes.c_uint]
gdi.GetDIBits.argtypes = [c_void_p, c_void_p, ctypes.c_uint, ctypes.c_uint,
                          c_void_p, c_void_p, ctypes.c_uint]
gdi.SelectObject.restype = c_void_p
gdi.SelectObject.argtypes = [c_void_p, c_void_p]
gdi.DeleteObject.argtypes = [c_void_p]
gdi.DeleteDC.argtypes = [c_void_p]
u32.GetDC.restype = c_void_p
u32.GetDC.argtypes = [c_void_p]
u32.ReleaseDC.argtypes = [c_void_p, c_void_p]
u32.DrawIconEx.argtypes = [c_void_p, c_int, c_int, c_void_p, c_int, c_int,
                           ctypes.c_uint, c_void_p, ctypes.c_uint]
u32.DestroyIcon.argtypes = [c_void_p]
u32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                      ctypes.c_void_p, ctypes.c_uint]
shell32.SHGetFileInfoW.restype = ctypes.c_size_t

DEFAULT_CONFIG = {
    "icon_size": 48,
    "gap": 10,
    "padding": 10,
    "radius": 18,
    "zoom": 1.75,              # 悬停时放大到几倍（1.5 / 1.75 / 2.0）
    "bg": "#1F1F24",
    "border": "#3A3A42",
    "topmost": True,
    "items": [],
}

# 面板里可切换的两种底色
THEMES = {
    "dark": {"bg": "#1F1F24", "border": "#3A3A42"},
    "light": {"bg": "#F7F7F9", "border": "#D8D8DE"},
}


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- PNG 写出
def png_write(path, w, h, bgra):
    """把 BGRA 原始像素写成 PNG（RGBA，带 alpha）"""
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)                      # filter: none
        off = y * stride
        row = bytearray(bgra[off:off + stride])
        for i in range(0, len(row), 4):    # BGRA -> RGBA
            row[i], row[i + 2] = row[i + 2], row[i]
        raw += row

    def chunk(tag, data):
        body = struct.pack(">I", len(data)) + tag + data
        return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    data += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


def hex_rgb(color):
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def flatten(bgra, w, h, bg_rgb):
    """把 alpha 压平到不透明底色：Tk 画不出半透明，压平后才不会有黑边"""
    br, bg_, bb = bg_rgb
    out = bytearray(bgra)
    for i in range(0, len(out), 4):
        a = out[i + 3]
        if a == 255:
            continue
        k = a / 255.0
        out[i] = int(out[i] * k + bb * (1 - k))
        out[i + 1] = int(out[i + 1] * k + bg_ * (1 - k))
        out[i + 2] = int(out[i + 2] * k + br * (1 - k))
        out[i + 3] = 255
    return bytes(out)


# ---------------------------------------------------------------- .lnk 解析
def resolve_lnk(path):
    """从 .lnk 里读出真实目标程序；失败返回 None"""
    try:
        with open(path, "rb") as f:
            d = f.read()
    except OSError:
        return None
    if len(d) < 76 or struct.unpack("<I", d[:4])[0] != 0x4C:
        return None
    flags, = struct.unpack("<I", d[20:24])
    off = 76
    if flags & 0x1:                                    # LinkTargetIDList
        size, = struct.unpack("<H", d[off:off + 2])
        off += 2 + size
    if flags & 0x2:                                    # LinkInfo
        base = off
        hdr, = struct.unpack("<I", d[base + 4:base + 8])
        lbp, = struct.unpack("<I", d[base + 16:base + 20])
        lbpu = 0
        if hdr >= 0x24:
            lbpu, = struct.unpack("<I", d[base + 28:base + 32])
        if lbpu:
            end = d.find(b"\x00\x00", base + lbpu)
            if end % 2:
                end += 1
            return d[base + lbpu:end].decode("utf-16-le", "ignore") or None
        if lbp:
            end = d.find(b"\x00", base + lbp)
            return d[base + lbp:end].decode("mbcs", "ignore") or None
    if flags & 0x200:                                  # EnvironmentVariableDataBlock
        while off + 8 <= len(d):
            size, = struct.unpack("<I", d[off:off + 4])
            if size == 0:
                break
            sig, = struct.unpack("<I", d[off + 4:off + 8])
            if sig == 0xA0000001:
                raw = d[off + 268:off + 788].decode("utf-16-le", "ignore")
                return os.path.expandvars(raw.split("\x00")[0]) or None
            off += size
    return None


# ---------------------------------------------------------------- 图标提取
def _icon_via_private(path, size):
    """私有但稳定的接口，能按指定像素提取 exe/dll/ico 的大图标"""
    hicons = (c_void_p * 1)()
    try:
        n = u32.PrivateExtractIconsW(path, 0, size, size, hicons, None, 1, 0)
    except AttributeError:
        return None
    if n and hicons[0]:
        return hicons[0]
    return None


def _icon_via_shell(path):
    class SHFILEINFO(ctypes.Structure):
        _fields_ = [("hIcon", c_void_p), ("iIcon", c_int),
                    ("dwAttributes", wt.DWORD),
                    ("szDisplayName", wt.WCHAR * 260),
                    ("szTypeName", wt.WCHAR * 80)]
    info = SHFILEINFO()
    r = shell32.SHGetFileInfoW(path, 0, byref(info), sizeof(info), 0x100)
    return info.hIcon if r and info.hIcon else None


def extract_icon_png(src, out, size, bg_rgb):
    """把一个 exe/lnk 的图标渲染成 PNG，返回是否成功"""
    target = src
    if src.lower().endswith(".lnk"):
        target = resolve_lnk(src) or src
    hicon = None
    if target.lower().endswith((".exe", ".dll", ".ico")):
        hicon = _icon_via_private(target, size)
    if not hicon:
        hicon = _icon_via_shell(src if os.path.exists(src) else target)
    if not hicon:
        return False

    hdc = u32.GetDC(None)
    mdc = gdi.CreateCompatibleDC(hdc)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bmi = BIH()
    bmi.biSize = sizeof(BIH)
    bmi.biWidth = size
    bmi.biHeight = -size          # 负值 = 自上而下，省一次翻转
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bits = c_void_p()
    hbmp = gdi.CreateDIBSection(mdc, byref(bmi), 0, byref(bits), None, 0)
    if not hbmp:
        gdi.DeleteDC(mdc)
        u32.ReleaseDC(None, hdc)
        u32.DestroyIcon(hicon)
        return False
    old = gdi.SelectObject(mdc, hbmp)
    ctypes.memset(bits, 0, size * size * 4)
    ok = u32.DrawIconEx(mdc, 0, 0, hicon, size, size, 0, None, 3)   # DI_NORMAL
    buf = ctypes.string_at(bits, size * size * 4)
    gdi.SelectObject(mdc, old)
    gdi.DeleteObject(hbmp)
    gdi.DeleteDC(mdc)
    u32.ReleaseDC(None, hdc)
    u32.DestroyIcon(hicon)
    if not ok:
        return False
    png_write(out, size, size, flatten(buf, size, size, bg_rgb))
    return True


# ---------------------------------------------------------------- 配置
def pick_default_items():
    """首次运行：从开始菜单和桌面里挑几个常见程序"""
    roots = [os.path.join(os.environ.get("APPDATA", ""),
                          r"Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")]
    pool = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".lnk"):
                    pool.setdefault(os.path.splitext(f)[0].lower(),
                                    os.path.join(dirpath, f))

    wanted = [("文件资源管理器", "file explorer", r"C:\Windows\explorer.exe"),
              ("Microsoft Edge", "microsoft edge", None),
              ("Google Chrome", "google chrome", None),
              ("Docker Desktop", "docker desktop", None),
              ("Git Bash", "git bash", None),
              ("WorkBuddy AI", "workbuddy ai", None),
              ("Antigravity", "antigravity", None),
              ("NVIDIA App", "nvidia app", None)]
    items, used = [], set()
    for label, key, icon in wanted:
        if key in pool and key not in used:
            item = {"name": label, "target": pool[key]}
            if icon:
                item["icon"] = icon
            items.append(item)
            used.add(key)
        if len(items) >= 7:
            break
    if not items:                                    # 极端情况兜底
        items.append({"name": "文件资源管理器", "target": "explorer.exe",
                      "icon": r"C:\Windows\explorer.exe"})
    items.append({"name": "设置", "target": "ms-settings:",
                  "icon": r"C:\Windows\ImmersiveControlPanel\SystemSettings.exe"})
    return items


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (OSError, ValueError) as e:
            log("配置读取失败，使用默认值: %s" % e)
    if not cfg.get("items"):
        cfg["items"] = pick_default_items()
        save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log("配置写入失败: %s" % e)


# ---------------------------------------------------------------- 主窗口
class Dock:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.items = cfg["items"]
        self.icon = int(cfg["icon_size"])
        self.gap = int(cfg["gap"])
        self.pad = int(cfg["padding"])
        self.radius = int(cfg["radius"])
        self.zoom = max(1.05, float(cfg.get("zoom", 1.75)))
        self.base_h = self.icon + self.pad * 2
        # 放大时图标顶部会溢出面板，窗口要留够高度
        self.grow = int(self.icon * (self.zoom - 1)) + 2
        # 放大时图标会溢出面板，窗口左右要留余量，否则边缘图标被裁掉
        self.slack = int(self.icon * 0.9)

        self.pill_w = self._calc_pill_width()
        self.width = self.pill_w + self.slack * 2
        self.height = self.base_h + self.grow

        self.images = {}          # (index, scale) -> PhotoImage
        self.scales = [1.0] * len(self.items)
        self.targets = [1.0] * len(self.items)
        self.layout = []          # [(x0, x1, index)]
        self.hover = -1
        self.animating = False
        self.menu = None

        self._setup_window()
        self._build_ui()
        self._load_icons()
        self._draw()
        root.after(120, self._animate)

    # -- 窗体 ------------------------------------------------------
    def _calc_pill_width(self):
        n = len(self.items)
        return self.pad * 2 + self.icon * n + self.gap * max(0, n - 1)

    def _setup_window(self):
        r = self.root
        r.title("Easy Dock")
        r.overrideredirect(True)
        if self.cfg.get("topmost", True):
            r.attributes("-topmost", True)
        r.wm_attributes("-transparentcolor", MAGIC)
        r.configure(bg=MAGIC)

        x, y = self._position()
        r.geometry("%dx%d+%d+%d" % (self.width, self.height, x, y))

    def _position(self):
        rect = wt.RECT()
        u32.SystemParametersInfoW(0x0030, 0, byref(rect), 0)   # SPI_GETWORKAREA
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right <= left or bottom <= top:
            left, top = 0, 0
            right, bottom = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = left + (right - left - self.width) // 2
        y = bottom - self.height - 8
        return x, y

    def _build_ui(self):
        cfg = self.cfg
        self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                bg=MAGIC, highlightthickness=0, bd=0)
        self.canvas.pack()

        # 底板：圆角矩形（半径用平滑多边形近似）
        x1, y1 = self.slack, self.grow
        x2, y2 = self.slack + self.pill_w, self.height
        self.canvas.create_polygon(self._round_rect(x1, y1, x2, y2, self.radius),
                                   fill=cfg["bg"], outline=cfg["border"], width=1)

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(-1))

    @staticmethod
    def _round_rect(x1, y1, x2, y2, r):
        pts = []
        steps = 8
        import math
        corners = [(x2 - r, y1 + r, -90, 0), (x2 - r, y2 - r, 0, 90),
                   (x1 + r, y2 - r, 90, 180), (x1 + r, y1 + r, 180, 270)]
        for cx, cy, a0, a1 in corners:
            for i in range(steps + 1):
                a = math.radians(a0 + (a1 - a0) * i / steps)
                pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
        return pts

    # -- 图标 ------------------------------------------------------
    def _load_icons(self):
        os.makedirs(ICON_DIR, exist_ok=True)
        bg_rgb = hex_rgb(self.cfg["bg"])
        src_size = self.icon * 2                      # 2 倍图，放大到 1.75 倍也够清晰
        for i, item in enumerate(self.items):
            target = item.get("target", "")
            src = item.get("icon") or target          # 允许单独指定图标来源
            is_uri = target.lower().startswith(("ms-settings:", "http:", "https:", "shell:"))
            if is_uri and not item.get("icon"):
                self.images[i] = None
                continue
            key = hashlib.sha1(("%s|%s|%d" % (src, self.cfg["bg"], src_size)).encode("utf-8")).hexdigest()[:16]
            png = os.path.join(ICON_DIR, key + ".png")
            if not os.path.exists(png):
                if not extract_icon_png(src, png, src_size, bg_rgb):
                    log("图标提取失败: %s" % src)
                    png = None
            self.images[i] = png

    def _scaled(self, index, scale):
        """按缩放倍数取（必要时生成）一张 PhotoImage；无图标返回 None"""
        from fractions import Fraction
        step = min(SCALES, key=lambda s: abs(s - scale))
        cache = self.images.setdefault("_cache", {})
        key = (index, step)
        if key in cache:
            return cache[key]
        path = self.images.get(index)
        if not path:
            return None
        base = tk.PhotoImage(file=path)
        # 源图是 icon*2，显示 icon*scale 即按 scale/2 缩放
        fr = Fraction(step / 2).limit_denominator(16)
        img = base.zoom(fr.numerator).subsample(fr.denominator)
        cache[key] = img
        return img

    # -- 绘制 ------------------------------------------------------
    def _draw(self):
        c = self.canvas
        c.delete("icon")
        baseline = self.height - self.pad
        widths = [self.icon * s for s in self.scales]
        total = sum(widths) + self.gap * max(0, len(self.items) - 1)
        x = (self.width - total) / 2.0
        self.layout = []
        for i, w in enumerate(widths):
            img = self._scaled(i, self.scales[i])
            cx = x + w / 2.0
            if img is not None:
                c.create_image(cx, baseline, image=img, anchor="s", tags="icon")
            else:
                self._draw_tile(c, cx, w, baseline, i)
            self.layout.append((x, x + w, i))
            x += w + self.gap

    def _draw_tile(self, c, cx, w, baseline, i):
        """没有可用图标时，画一个圆角磁贴 + 程序名首字"""
        x1, y1 = cx - w / 2.0, baseline - w
        pts = self._round_rect(x1, y1, x1 + w, baseline, max(6, int(w * 0.22)))
        c.create_polygon(pts, fill="#3A3A44", outline="#4A4A55", width=1, tags="icon")
        name = self.items[i].get("name", "?")
        c.create_text(cx, y1 + w / 2.0, text=name[:1],
                      fill="#FFFFFF", tags="icon",
                      font=("Microsoft YaHei UI", max(8, int(w * 0.4)), "bold"))

    # -- 交互 ------------------------------------------------------
    def _index_at(self, x):
        for x0, x1, i in self.layout:
            if x0 <= x <= x1:
                return i
        return -1

    def _on_motion(self, ev):
        self._set_hover(self._index_at(ev.x))

    def _set_hover(self, index):
        if index == self.hover:
            return
        self.hover = index
        for i in range(len(self.items)):
            if index < 0:
                self.targets[i] = 1.0
            else:
                d = abs(i - index)
                near = 1.0 + (self.zoom - 1.0) / 2.0
                self.targets[i] = {0: self.zoom, 1: near}.get(d, 1.0)
        if not self.animating:
            self.animating = True
            self._animate()

    def _animate(self):
        moved = False
        for i in range(len(self.scales)):
            diff = self.targets[i] - self.scales[i]
            if abs(diff) > 0.01:
                self.scales[i] += diff * 0.35
                moved = True
            else:
                self.scales[i] = self.targets[i]
        self._draw()
        if moved:
            self.root.after(16, self._animate)
        else:
            self.animating = False

    def _on_click(self, ev):
        i = self._index_at(ev.x)
        if i >= 0:
            self.launch(i)

    def launch(self, i):
        item = self.items[i]
        target = item.get("target", "")
        try:
            if target.lower().startswith(("ms-settings:", "http:", "https:", "shell:")):
                shell32.ShellExecuteW(None, "open", target, None, None, 1)
            elif os.path.exists(target):
                shell32.ShellExecuteW(None, "open", target, item.get("args") or None,
                                      os.path.dirname(target) or None, 1)
            else:
                log("目标不存在: %s" % target)
                messagebox.showwarning("Easy Dock", "找不到该程序：\n%s" % target)
        except Exception as e:                       # noqa: BLE001
            log("启动失败 %s: %s" % (target, e))

    def _on_right(self, ev):
        i = self._index_at(ev.x)
        m = tk.Menu(self.root, tearoff=0)
        if i >= 0:
            m.add_command(label="打开 " + self.items[i].get("name", ""),
                          command=lambda: self.launch(i))
            m.add_command(label="从 Dock 移除",
                          command=lambda: self.remove(i))
            m.add_separator()
        m.add_command(label="添加应用…", command=self.add)
        m.add_command(label="重新居中", command=self.recenter)
        m.add_separator()
        m.add_command(label="退出 Dock", command=self.quit_app)
        self.menu = m
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def quit_app(self):
        try:
            os.remove(os.path.join(HERE, "dock.pid"))
        except OSError:
            pass
        self.root.destroy()

    def add(self):
        path = filedialog.askopenfilename(
            title="选择要放到 Dock 的程序",
            filetypes=[("程序", "*.exe;*.lnk;*.bat;*.cmd"), ("所有文件", "*.*")])
        if not path:
            return
        self.items.append({"name": os.path.splitext(os.path.basename(path))[0],
                           "target": path})
        self._rebuild(keep_geometry=False)

    def remove(self, i):
        if not (0 <= i < len(self.items)):
            return
        del self.items[i]
        self._rebuild(keep_geometry=False)

    def recenter(self):
        x, y = self._position()
        self.root.geometry("+%d+%d" % (x, y))

    def _rebuild(self, keep_geometry=False):
        self.cfg["items"] = self.items
        save_config(self.cfg)
        self.scales = [1.0] * len(self.items)
        self.targets = [1.0] * len(self.items)
        self.images = {}
        self.hover = -1
        self.pill_w = self._calc_pill_width()
        self.width = self.pill_w + self.slack * 2
        self.canvas.destroy()
        self._build_ui()
        self._load_icons()
        x, y = self._position()
        self.root.geometry("%dx%d+%d+%d" % (self.width, self.height, x, y))
        self._draw()


# ---------------------------------------------------------------- 截图自检
def grab_png(x, y, w, h, path):
    """按屏幕坐标截一块图，用于开发期自查"""
    hdc = u32.GetDC(None)
    mdc = gdi.CreateCompatibleDC(hdc)
    hbmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mdc, hbmp)
    gdi.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)     # SRCCOPY

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bmi = BIH()
    bmi.biSize = sizeof(BIH)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mdc, hbmp, 0, h, buf, byref(bmi), 0)
    gdi.DeleteObject(hbmp)
    gdi.DeleteDC(mdc)
    u32.ReleaseDC(None, hdc)
    png_write(path, w, h, bytes(buf.raw))


def run_shot(cfg):
    """开发期自检：起窗 → 模拟悬停 → 截图 → 退出"""
    root = tk.Tk()
    dock = Dock(root, cfg)
    out = os.path.join(HERE, "shot.png")

    def do_hover():
        dock._set_hover(min(3, len(dock.items) - 1))

    def do_grab():
        x, y = dock._position()
        grab_png(x - 12, y - 12, dock.width + 24, dock.height + 24, out)
        root.after(120, root.destroy)

    root.after(500, do_hover)
    root.after(900, do_grab)
    root.mainloop()
    print("截图:", out, os.path.getsize(out) if os.path.exists(out) else "失败")
    print("图标:", sum(1 for k, v in dock.images.items() if isinstance(k, int) and v))


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:                                # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                            # noqa: BLE001
            pass
    cfg = load_config()
    if "--shot" in sys.argv:
        run_shot(cfg)
        return
    root = tk.Tk()
    dock = Dock(root, cfg)

    pid_file = os.path.join(HERE, "dock.pid")
    try:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    def on_close():
        try:
            os.remove(pid_file)
        except OSError:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    try:
        os.remove(pid_file)
    except OSError:
        pass
    del dock


if __name__ == "__main__":
    main()
