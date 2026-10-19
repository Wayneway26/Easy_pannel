# -*- coding: utf-8 -*-
"""
backdrop.py —— 给 Tk 窗口做「磨砂玻璃」底

原理
----
Tk 没有实时模糊，但它能显示带圆角的 PNG 图片。所以：
  1. 窗口还没显示时，抓一块桌面像素（不会拍到自己）
  2. 缩到 1/6，做几遍 box blur
  3. 和一层色（深色/浅色蒙层）按比例混合 —— 得到「玻璃」色调
  4. 放大回原尺寸，圆角外的像素涂成 MAGIC（色键），交给窗口抠掉
图片最后贴到 Canvas 最底层，就成了 Seelen UI 那种浮在模糊桌面上的玻璃面板。

换壁纸 / 桌面变化后调用 refresh() 重新生成一次即可。
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import zlib
from ctypes import byref, c_int, c_uint, c_void_p

u32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32

gdi.CreateCompatibleDC.restype = c_void_p
gdi.CreateCompatibleDC.argtypes = [c_void_p]
gdi.CreateCompatibleBitmap.restype = c_void_p
gdi.CreateCompatibleBitmap.argtypes = [c_void_p, c_int, c_int]
gdi.SelectObject.restype = c_void_p
gdi.SelectObject.argtypes = [c_void_p, c_void_p]
gdi.BitBlt.argtypes = [c_void_p, c_int, c_int, c_int, c_int, c_void_p,
                       c_int, c_int, c_uint]
gdi.GetDIBits.argtypes = [c_void_p, c_void_p, c_uint, c_uint, c_void_p,
                          c_void_p, c_uint]
gdi.DeleteObject.argtypes = [c_void_p]
gdi.DeleteDC.argtypes = [c_void_p]
u32.GetDC.restype = c_void_p
u32.GetDC.argtypes = [c_void_p]
u32.ReleaseDC.argtypes = [c_void_p, c_void_p]

MAGIC_RGB = (1, 2, 3)          # 与 MAGIC #010203 对应，圆角外用它抠透明


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


# ---------------------------------------------------------------- 抓屏
def grab(x, y, w, h):
    """抓一块屏幕，返回 BGRA 字节（top-down）"""
    w, h = max(1, int(w)), max(1, int(h))
    hdc = u32.GetDC(None)
    mdc = gdi.CreateCompatibleDC(hdc)
    hbmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    old = gdi.SelectObject(mdc, hbmp)
    gdi.BitBlt(mdc, 0, 0, w, h, hdc, int(x), int(y), 0x00CC0020)   # SRCCOPY

    bmi = _BIH()
    bmi.biSize = ctypes.sizeof(_BIH)
    bmi.biWidth = w
    bmi.biHeight = -h                    # 负数 = top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mdc, hbmp, 0, h, buf, byref(bmi), 0)

    gdi.SelectObject(mdc, old)
    gdi.DeleteObject(hbmp)
    gdi.DeleteDC(mdc)
    u32.ReleaseDC(None, hdc)
    return bytearray(buf.raw)


# ---------------------------------------------------------------- 图像处理
def _sample(raw, w, h, sw, sh, s):
    """按块取中心点，缩到 sw×sh"""
    out = bytearray(sw * sh * 4)
    for yy in range(sh):
        sy = min(h - 1, yy * s + s // 2)
        row = sy * w
        for xx in range(sw):
            sx = min(w - 1, xx * s + s // 2)
            i = (row + sx) * 4
            j = (yy * sw + xx) * 4
            out[j:j + 4] = raw[i:i + 4]
    return out


def _blur_v(buf, w, h, r):
    out = bytearray(len(buf))
    span = r * 2 + 1
    for x in range(w):
        for ch in range(3):
            acc = 0
            for k in range(-r, r + 1):
                yy = min(h - 1, max(0, k))
                acc += buf[(yy * w + x) * 4 + ch]
            for y in range(h):
                out[(y * w + x) * 4 + ch] = acc // span
                y_out = min(h - 1, max(0, y - r))
                y_in = min(h - 1, max(0, y + r + 1))
                acc += buf[(y_in * w + x) * 4 + ch] - buf[(y_out * w + x) * 4 + ch]
    return out


def _blur_h(buf, w, h, r):
    out = bytearray(len(buf))
    span = r * 2 + 1
    for y in range(h):
        base = y * w * 4
        for ch in range(3):
            acc = 0
            for k in range(-r, r + 1):
                xx = min(w - 1, max(0, k))
                acc += buf[base + xx * 4 + ch]
            for x in range(w):
                out[base + x * 4 + ch] = acc // span
                x_out = min(w - 1, max(0, x - r))
                x_in = min(w - 1, max(0, x + r + 1))
                acc += buf[base + x_in * 4 + ch] - buf[base + x_out * 4 + ch]
    return out


def _blur(buf, w, h, r=2, passes=3):
    for _ in range(passes):
        buf = _blur_h(buf, w, h, r)
        buf = _blur_v(buf, w, h, r)
    return buf


def _mix(buf, tint, opacity):
    """和蒙层色混合：opacity 越大越不透明"""
    o = max(0.0, min(1.0, opacity))
    tr, tg, tb = tint
    for i in range(0, len(buf), 4):
        buf[i] = int(buf[i] * (1 - o) + tb * o)
        buf[i + 1] = int(buf[i + 1] * (1 - o) + tg * o)
        buf[i + 2] = int(buf[i + 2] * (1 - o) + tr * o)


def _bgra_to_rgba(buf):
    for i in range(0, len(buf), 4):
        buf[i], buf[i + 2] = buf[i + 2], buf[i]
        buf[i + 3] = 255


def _upscale(small, sw, sh, w, h, s):
    out = bytearray(w * h * 4)
    for y in range(h):
        sy = min(sh - 1, y // s)
        srow = small[sy * sw * 4:(sy + 1) * sw * 4]
        if not srow:
            continue
        full = b"".join(srow[i:i + 4] * s for i in range(0, len(srow), 4))
        if len(full) < w * 4:                     # 宽度除不尽时补最后一列
            full += srow[-4:] * w
        out[y * w * 4:(y + 1) * w * 4] = full[:w * 4]
    return out


def _round_corners(buf, w, h, r):
    """圆角外涂 MAGIC，窗口用色键把它抠成真透明"""
    r = int(max(0, min(r, w // 2, h // 2)))
    if r <= 0:
        return
    mr, mg, mb = MAGIC_RGB
    # (区域起点x, 区域起点y, 局部圆心x, 局部圆心y)
    regions = [(0, 0, r, r), (w - r, 0, 0, r),
               (0, h - r, r, 0), (w - r, h - r, 0, 0)]
    for ox, oy, ccx, ccy in regions:
        for yy in range(r):
            for xx in range(r):
                if (xx - ccx) ** 2 + (yy - ccy) ** 2 > r * r:
                    i = ((oy + yy) * w + ox + xx) * 4
                    buf[i], buf[i + 1], buf[i + 2] = mr, mg, mb


def _png(path, w, h, rgba):
    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * w * 4:(y + 1) * w * 4]
    data = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 对外接口
def make(path, x, y, w, h, radius=18, tint=(30, 30, 36), opacity=0.62,
         scale=6, blur=2, passes=3):
    """生成一张玻璃底图。tint 是蒙层色，opacity 越大越不透明。
    返回 (图片路径, 平均色RGB) —— 平均色可以给图标压平用，避免边缘出现色块。"""
    w, h = int(w), int(h)
    raw = grab(x, y, w, h)
    sw, sh = max(1, (w + scale - 1) // scale), max(1, (h + scale - 1) // scale)
    small = _sample(raw, w, h, sw, sh, scale)
    small = _blur(small, sw, sh, blur, passes)
    _mix(small, tint, opacity)
    _bgra_to_rgba(small)

    n = sw * sh
    avg = (sum(small[i] for i in range(0, len(small), 4)) // n,
           sum(small[i + 1] for i in range(0, len(small), 4)) // n,
           sum(small[i + 2] for i in range(0, len(small), 4)) // n)

    big = _upscale(small, sw, sh, w, h, scale)
    _round_corners(big, w, h, radius)
    _png(path, w, h, big)
    return path, avg
