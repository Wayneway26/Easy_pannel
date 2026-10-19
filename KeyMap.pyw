# -*- coding: utf-8 -*-
"""
KeyMap.pyw —— 全局按键重映射（Ctrl+C → Alt+C 这类）

原理
----
用 WH_KEYBOARD_LL 低级键盘钩子拦在系统最前面。难点不是拦截，而是
「修饰键不能提前放行」：如果 Ctrl 先被放出去，等 C 按下来时系统状态已经是
「Ctrl 按下」，再发 Alt+C 就成了 Ctrl+Alt+C。

所以这里用「事务式修饰键」：
  · 修饰键按下先不发给系统，挂起（pending）
  · 若下一个键命中规则 → 丢弃挂起的修饰键，发目标组合
  · 若不命中 → 先把挂起的修饰键补发出去，再放行这个键
  · 按住修饰键超过 320ms 没有后续键 → 也补发（保证 Ctrl 单独按能用）
  · 放行前已经发出去的修饰键，在替换时临时 keyup，发完再 keydown 还原

自己 SendInput 出去的键带 dwExtraInfo 标记，回调里直接放行，避免递归。

安全
----
· 独立进程，面板可随时 taskkill，钩子随进程退出自动卸载
· 回调里只做集合判断 + SendInput，不读写磁盘，不会触发钩子超时被系统摘掉
· 默认 enabled=false，用户在面板里手动打开
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "keymap.json")
PID_PATH = os.path.join(HERE, "keymap.pid")
LOG_PATH = os.path.join(HERE, "keymap.log")

MAGIC = 0x0EA5E1      # dwExtraInfo 标记，认领自己发出的按键

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

u32.SetWindowsHookExW.restype = ctypes.c_void_p
u32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
u32.CallNextHookEx.restype = ctypes.c_ssize_t
u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), ctypes.c_void_p, wt.UINT, wt.UINT]
u32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.SetTimer.restype = ctypes.c_size_t
u32.SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
u32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
u32.SendInput.restype = wt.UINT
u32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
u32.DispatchMessageW.restype = ctypes.c_ssize_t
k32.GetCurrentThreadId.restype = wt.DWORD

MODS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
MOD_VK = set(MODS.values())

VK = dict(MODS)
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)
for _i in range(1, 13):
    VK["f%d" % _i] = 0x6F + _i
VK.update({"space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
           "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23,
           "pageup": 0x21, "pagedown": 0x22,
           "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
           "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
           "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF})

DEFAULT_CONFIG = {"enabled": False, "rules": []}
HOLD_FLUSH_MS = 320


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = dict(DEFAULT_CONFIG)
    cfg.setdefault("enabled", False)
    cfg.setdefault("rules", [])
    return cfg


def parse(combo):
    """'ctrl+shift+c' -> (frozenset({'ctrl','shift'}), 'c')，非法返回 None"""
    parts = [p.strip().lower() for p in str(combo).split("+") if p.strip()]
    if not parts:
        return None
    mods, key = set(), None
    for p in parts:
        if p in MODS:
            mods.add(p)
        elif key is None:
            key = p
        else:
            return None
    if key is None or key not in VK or key in MODS:
        return None
    return frozenset(mods), key


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)


def _send(vk, up):
    inp = INPUT()
    inp.type = 1                      # INPUT_KEYBOARD
    inp.u.ki = KEYBDINPUT(vk, 0, 0x0002 if up else 0, 0, MAGIC)
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Engine:
    def __init__(self, cfg):
        self.enabled = bool(cfg.get("enabled"))
        self.rules = []
        for r in cfg.get("rules", []):
            a, b = parse(r.get("from")), parse(r.get("to"))
            if a and b:
                self.rules.append((a[0], a[1], b[0], b[1]))
        self.down = set()            # 当前按下的修饰键（真实状态）
        self.pending = []            # [(vk, ts)] 还没发给系统的修饰键
        self.sent_mods = set()       # 已经发给系统的修饰键
        self.suppress_up = set()     # 主键已处理，抬起时要吞掉
        self.tid = 0

    # -- 键的发送 --------------------------------------------------
    def flush_pending(self):
        for vk, _ts in self.pending:
            _send(vk, False)
            self.sent_mods.add(vk)
        self.pending = []

    def flush_stale(self):
        if not self.pending:
            return
        now = time.time() * 1000
        if now - self.pending[0][1] >= HOLD_FLUSH_MS:
            self.flush_pending()

    def match(self, key):
        mods = frozenset(m for m, vk in MODS.items() if vk in self.down)
        for f_mods, f_key, t_mods, t_key in self.rules:
            if f_key == key and f_mods == mods:
                return t_mods, t_key
        return None

    def do_replace(self, t_mods, t_key):
        # 1. 已经发出去的修饰键先松开，否则会变成 Ctrl+Alt+C
        for vk in list(self.sent_mods):
            _send(vk, True)
        self.sent_mods.clear()
        # 2. 挂起的直接丢弃（系统还不知道它们被按过）
        self.pending = []
        # 3. 发目标组合
        for name in t_mods:
            _send(MODS[name], False)
        _send(VK[t_key], False)
        _send(VK[t_key], True)
        for name in reversed(sorted(t_mods)):
            _send(MODS[name], True)
        # 4. 还原用户仍按着的修饰键
        for vk in list(self.down):
            _send(vk, False)
            self.sent_mods.add(vk)

    # -- 钩子 ------------------------------------------------------
    def on_key(self, ncode, wparam, lparam):
        if ncode != 0:
            return u32.CallNextHookEx(None, ncode, wparam, lparam)
        kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if kb.dwExtraInfo == MAGIC:          # 自己发的，放行
            return u32.CallNextHookEx(None, ncode, wparam, lparam)
        if not self.enabled or not self.rules:
            return u32.CallNextHookEx(None, ncode, wparam, lparam)

        vk = kb.vkCode
        is_up = wparam in (WM_KEYUP, WM_SYSKEYUP)

        if vk in MOD_VK:
            if is_up:
                self.down.discard(vk)
                for i, (pv, _t) in enumerate(self.pending):
                    if pv == vk:
                        del self.pending[i]          # 单独按了一下修饰键
                        _send(vk, False)
                        _send(vk, True)
                        return 1
                return u32.CallNextHookEx(None, ncode, wparam, lparam)
            if vk in self.down:                      # 自动重复
                return 1
            self.down.add(vk)
            self.pending.append((vk, time.time() * 1000))
            return 1                                 # 先扣着，等主键决定

        # 主键
        if is_up:
            if vk in self.suppress_up:
                self.suppress_up.discard(vk)
                return 1
            return u32.CallNextHookEx(None, ncode, wparam, lparam)

        key = None
        for name, code in VK.items():
            if code == vk:
                key = name
                break
        if key is None:
            self.flush_pending()
            return u32.CallNextHookEx(None, ncode, wparam, lparam)

        hit = self.match(key)
        if hit:
            self.do_replace(*hit)
            self.suppress_up.add(vk)
            return 1
        self.flush_pending()
        return u32.CallNextHookEx(None, ncode, wparam, lparam)


def main():
    cfg = load_config()
    if not cfg.get("enabled"):
        log("未启用，退出")
        return
    eng = Engine(cfg)
    if not eng.rules:
        log("没有规则，退出")
        return
    log("启动，规则 %d 条" % len(eng.rules))

    proc = HOOKPROC(eng.on_key)
    hook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
    if not hook:
        log("钩子安装失败 err=%s" % ctypes.get_last_error())
        return

    try:
        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    eng.tid = k32.GetCurrentThreadId()
    u32.SetTimer(None, 0, 60, None)      # 定时器消息：兜底补发挂起的修饰键
    msg = wt.MSG()
    try:
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0113:    # WM_TIMER
                eng.flush_stale()
                continue
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
    finally:
        u32.UnhookWindowsHookEx(hook)
        try:
            os.remove(PID_PATH)
        except OSError:
            pass
        log("退出")


if __name__ == "__main__":
    main()
