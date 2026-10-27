# -*- coding: utf-8 -*-
"""原生 Windows 表单（**纯 ctypes，零依赖**）。

为什么不用 tkinter
──────────────────
WorkBuddy 自带的托管 Python 3.13 **不带 tkinter**，而财务人员的办公电脑上多半也没有
自己装过带 tkinter 的 Python。凭据录入这种事不能押在"用户机器上恰好有某个 Python"上，
所以这里直接调 user32/gdi32 画一个普通 Windows 窗口：

  · 只用 `ctypes` + 系统 DLL，**本技能依旧零第三方依赖**；
  · 任何 Windows 都能跑，不需要装运行时、不改 PATH；
  · 有真正的输入框、密码框、复选框、按钮 —— 和用户平时见的窗口一致；
  · 密码框用 `ES_PASSWORD`，勾「显示密码」才明文显示。

边界（刻意如此）
────────────────
· 只做**金蝶连接**那几个字段。发票云的多组织动态卡片仍交给文字向导 ——
  在原生控件里做动态增删 + 滚动会把这段代码从 300 行推到 800 行，不值当。
· 不做自定义绘制/皮肤，保持系统原生外观（用户认得、信得过）。
· 本模块**不碰凭据存储**：只把用户填的内容以 dict 返回，保存由调用方做。

调用约定
────────
    run_form(change_key=False, prefill={...}) -> dict | None
      返回 {"base_url","acct_name","username","password","want_piazzone"}
      用户取消 / 直接关窗 → None（调用方据此按"没改成"处理）

⚠️ 实现注意（踩过的坑）
──────────────────────
· WNDPROC 的回调对象**必须持有强引用**，否则被 GC 后窗口过程变成野指针 → 窗口一创建就崩。
· 64 位下 WPARAM/LPARAM 是 8 字节：`c_size_t` / `c_ssize_t`，写成 c_int 会在 64 位下截断。
· 中文输入法里回车是"确认候选词"：若在消息循环里无条件拦 VK_RETURN，中文输入第一个字就被提交了。
  所以拦截前先用 imm32 查一次是否正在组词（`_ime_composing`）。
· 高 DPI 不处理会整体糊成一片，且 150% 缩放下窗口小得没法看 —— 先声明 per-monitor v2，再按 DPI 缩放。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

CLASS_NAME = "KingdeeCredFormW"
TITLE_NORMAL = "金蝶云星空 · 凭据配置"
TITLE_CHANGE = "金蝶云星空 · 修改密钥"

# ── 控件 ID ──
ID_BASE_URL = 1001
ID_ACCT_NAME = 1002
ID_USERNAME = 1003
ID_PASSWORD = 1004
ID_PASSWORD2 = 1005
ID_SHOW_PWD = 1006
ID_WANT_PZ = 1007
ID_SAVE = 1008
ID_CANCEL = 1009

# ── 样式常量 ──
WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
ES_AUTOHSCROLL = 0x0080
ES_PASSWORD = 0x0020
BS_DEFPUSHBUTTON = 0x0001
BS_AUTOCHECKBOX = 0x0003
SS_RIGHT = 0x00000002
SS_LEFT = 0x00000000
SW_SHOW = 5
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETFONT = 0x0030
WM_KEYDOWN = 0x0100
WM_COMMAND = 0x0111
WM_GETDLGCODE = 0x0087
EM_SETPASSWORDCHAR = 0x00CC
BM_SETCHECK = 0x00F1
BM_GETCHECK = 0x00F0
BST_CHECKED = 1
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
MB_OK = 0x00000000
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_TOPMOST = 0x00040000
DEFAULT_CHARSET = 1
CLEARTYPE_QUALITY = 5
FW_NORMAL = 400
FW_BOLD = 700
GCS_COMPSTR = 0x0008
SM_CXSCREEN = 0
SM_CYSCREEN = 1
LOGPIXELSX = 88

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint),
                ("pt", POINT)]


def available() -> bool:
    """原生表单能不能用（只在 Windows 上可用）。"""
    return sys.platform == "win32"


# ─────────────────────────── DLL 绑定 ───────────────────────────

def _bind():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = ctypes.c_ushort
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = LRESULT
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = LRESULT
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                                   ctypes.c_uint, ctypes.c_uint]
    user32.GetMessageW.restype = ctypes.c_int
    user32.IsDialogMessageW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MSG)]
    user32.IsDialogMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.SetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UpdateWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetFocus.argtypes = [ctypes.c_void_p]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.AdjustWindowRectEx.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD,
                                          wintypes.BOOL, wintypes.DWORD]
    user32.MessageBoxW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   ctypes.c_uint]
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.InvalidateRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL]

    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD, wintypes.DWORD,
                                                       wintypes.DWORD, wintypes.DWORD,
                                                       wintypes.DWORD, wintypes.DWORD,
                                                       wintypes.DWORD, wintypes.DWORD,
                                                       wintypes.LPCWSTR]
    gdi32.CreateFontW.restype = ctypes.c_void_p
    gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p

    try:
        imm32 = ctypes.WinDLL("imm32")
        imm32.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmGetCompositionStringW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                   ctypes.c_void_p, wintypes.DWORD]
        imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        imm32.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    except OSError:
        imm32 = None

    return user32, gdi32, kernel32, imm32


_USER32, _GDI32, _KERNEL32, _IMM32 = (_bind() if sys.platform == "win32"
                                      else (None, None, None, None))


def _dpi_scale() -> tuple[float, int]:
    """声明 DPI 感知并返回 (缩放系数, dpi)。不处理的话 150% 缩放下窗口小到没法看。"""
    dpi = 96
    try:                                   # per-monitor v2（Win10 1703+）
        fn = _USER32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        fn(ctypes.c_void_p(-4))
    except Exception:  # noqa: BLE001
        try:
            _USER32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    try:
        _USER32.GetDpiForSystem.argtypes = []
        _USER32.GetDpiForSystem.restype = ctypes.c_uint
        dpi = int(_USER32.GetDpiForSystem()) or 96
    except Exception:  # noqa: BLE001
        try:
            hdc = _USER32.GetDC(None)
            dpi = _GDI32.GetDeviceCaps(ctypes.c_void_p(hdc), LOGPIXELSX) or 96
            _USER32.ReleaseDC(None, ctypes.c_void_p(hdc))
        except Exception:  # noqa: BLE001
            dpi = 96
    return max(dpi / 96.0, 1.0), dpi


def _ime_composing(hwnd) -> bool:
    """输入法是否正在组词 —— 组词中的回车要留给输入法。"""
    if not _IMM32 or not hwnd:
        return False
    try:
        ctx = _IMM32.ImmGetContext(ctypes.c_void_p(hwnd))
        if not ctx:
            return False
        try:
            return _IMM32.ImmGetCompositionStringW(
                ctypes.c_void_p(ctx), GCS_COMPSTR, None, 0) > 0
        finally:
            _IMM32.ImmReleaseContext(ctypes.c_void_p(hwnd), ctypes.c_void_p(ctx))
    except Exception:  # noqa: BLE001
        return False


def _text(hwnd) -> str:
    n = _USER32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _USER32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, n + 1)
    return buf.value


# ─────────────────────────── 表单 ───────────────────────────

def message(text: str, title: str = "提示", warning: bool = False) -> None:
    """原生提示框（原生表单没有 tkinter 的 messagebox，用它给用户反馈结果）。"""
    if not available():
        print(text)
        return
    flags = MB_OK | (MB_ICONWARNING if warning else MB_ICONINFORMATION) | MB_TOPMOST
    _USER32.MessageBoxW(None, text, title, flags)


def run_form(change_key: bool = False, prefill: dict | None = None,
             has_piazzone: bool = False) -> dict | None:
    """弹出原生表单。返回填写结果；取消返回 None。"""
    if not available():
        raise OSError("原生表单仅支持 Windows")

    prefill = prefill or {}
    scale, dpi = _dpi_scale()

    def s(v: float) -> int:
        return int(round(v * scale))

    h_inst = _KERNEL32.GetModuleHandleW(None)
    font = _GDI32.CreateFontW(-s(13), 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                              0, 0, CLEARTYPE_QUALITY, 0, "Microsoft YaHei UI")
    font_bold = _GDI32.CreateFontW(-s(14), 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                                   0, 0, CLEARTYPE_QUALITY, 0, "Microsoft YaHei UI")

    state: dict = {"hwnd": None, "edits": {}, "checks": {}, "done": None, "dialogproc": None}
    widgets: list = []

    # ── 布局（单位：未缩放的逻辑像素）──
    MARGIN, LABEL_W, GAP, EDIT_W, ROW_H, ROW_GAP = 20, 104, 10, 300, 28, 12
    rows = [("base_url", "服务器地址", str(prefill.get("base_url") or "")),
            ("acct_name", "公司名 / 账套", str(prefill.get("acct_name") or "")),
            ("username", "用户名", str(prefill.get("username") or "")),
            ("password", "新密码" if change_key else "密码", "")]
    if change_key:
        rows.append(("password2", "再输一次", ""))

    client_w = MARGIN * 2 + LABEL_W + GAP + EDIT_W
    y = MARGIN
    y += s(24) + s(18) + s(14)                      # 标题 + 副标题 + 间距
    top_rows = y
    y += len(rows) * s(ROW_H + ROW_GAP)
    y_show = y
    y += s(24) + s(12)
    y_pz = y
    y += s(24) + s(12)
    y_hint = y
    y += s(20) + s(10)
    y_btn = y
    y += s(30)
    client_h = y + MARGIN

    style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX

    def _wndproc(hwnd, msg, wparam, lparam):
        try:
            if msg == WM_COMMAND:
                cid = int(wparam) & 0xFFFF
                code = (int(wparam) >> 16) & 0xFFFF
                if code == 0:                      # BN_CLICKED
                    if cid == ID_SAVE:
                        _do_save(hwnd)
                    elif cid == ID_CANCEL:
                        state["done"] = None
                        _USER32.DestroyWindow(ctypes.c_void_p(hwnd))
                    elif cid == ID_SHOW_PWD:
                        _apply_show(hwnd)
                return 0
            if msg == WM_CLOSE:
                state["done"] = None
                _USER32.DestroyWindow(ctypes.c_void_p(hwnd))
                return 0
            if msg == WM_DESTROY:
                _USER32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001  窗口过程里绝不能抛
            pass
        return _USER32.DefWindowProcW(ctypes.c_void_p(hwnd), msg, wparam, lparam)

    wndproc = WNDPROC(_wndproc)
    state["dialogproc"] = wndproc                   # ★ 必须持引用，否则回调被 GC

    wc = WNDCLASSW()
    wc.style = 0
    wc.lpfnWndProc = wndproc
    wc.cbClsExtra = 0
    wc.cbWndExtra = 0
    wc.hInstance = h_inst
    wc.hIcon = None
    wc.hCursor = _USER32.LoadCursorW(None, ctypes.c_void_p(32512))   # IDC_ARROW
    wc.hbrBackground = ctypes.c_void_p(16)                           # COLOR_BTNFACE+1（原生对话框底色）
    wc.lpszMenuName = None
    wc.lpszClassName = CLASS_NAME
    _USER32.RegisterClassW(ctypes.byref(wc))        # 已注册过会失败，忽略即可

    rect = RECT(0, 0, s(client_w), s(client_h))
    _USER32.AdjustWindowRectEx(ctypes.byref(rect), style, False, 0)
    win_w, win_h = rect.right - rect.left, rect.bottom - rect.top
    x = max((_USER32.GetSystemMetrics(SM_CXSCREEN) - win_w) // 2, 0)
    ypos = max(min((_USER32.GetSystemMetrics(SM_CYSCREEN) - win_h) // 3, 400), 0)

    hwnd = _USER32.CreateWindowExW(0, CLASS_NAME,
                                   TITLE_CHANGE if change_key else TITLE_NORMAL,
                                   style, x, ypos, win_w, win_h,
                                   None, None, h_inst, None)
    if not hwnd:
        raise OSError(ctypes.get_last_error(), "CreateWindowExW 失败")
    state["hwnd"] = hwnd

    def add(cls, text, st, cx, cy, cw, ch, cid, fnt=None):
        h = _USER32.CreateWindowExW(0, cls, text, st, cx, cy, cw, ch,
                                    ctypes.c_void_p(hwnd), ctypes.c_void_p(cid),
                                    ctypes.c_void_p(h_inst), None)
        if h and fnt is not None:
            _USER32.SendMessageW(ctypes.c_void_p(h), WM_SETFONT, fnt, 1)
        widgets.append(h)
        return h

    # 标题 + 副标题
    add("STATIC", "金蝶云星空 · 敏感信息录入", WS_CHILD | WS_VISIBLE | SS_LEFT,
        s(MARGIN), s(MARGIN), s(client_w - MARGIN * 2), s(24), 0, font_bold)
    add("STATIC", "凭据在本机加密保存（DPAPI），不进对话记录、不写明文文件",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        s(MARGIN), s(MARGIN + 24), s(client_w - MARGIN * 2), s(18), 0, font)

    # 字段行
    for i, (key, label, val) in enumerate(rows):
        ry = top_rows + i * s(ROW_H + ROW_GAP)
        add("STATIC", label, WS_CHILD | WS_VISIBLE | SS_RIGHT,
            s(MARGIN), ry + s(6), s(LABEL_W), s(20), 0, font)
        st = WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL
        if key in ("password", "password2"):
            st |= ES_PASSWORD
        edit = add("EDIT", "", st, s(MARGIN + LABEL_W + GAP), ry, s(EDIT_W), s(ROW_H),
                   {"base_url": ID_BASE_URL, "acct_name": ID_ACCT_NAME,
                    "username": ID_USERNAME, "password": ID_PASSWORD,
                    "password2": ID_PASSWORD2}[key], font)
        if val:
            _USER32.SetWindowTextW(ctypes.c_void_p(edit), val)
        state["edits"][key] = edit

    # 显示密码
    chk_show = add("BUTTON", "显示密码", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
                   s(MARGIN + LABEL_W + GAP), y_show, s(120), s(24), ID_SHOW_PWD, font)
    state["checks"]["show"] = chk_show

    # 发票云（勾了才在保存后走文字向导维护；不勾 = 原样保留已存的配置）
    pz_label = ("同时维护发票云（已存的会保留，不改就别勾）" if has_piazzone
                else "同时维护发票云（可选，仅验票/服务端上传需要）")
    chk_pz = add("BUTTON", pz_label,
                 WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX,
                 s(MARGIN), y_pz, s(client_w - MARGIN * 2), s(24), ID_WANT_PZ, font)
    state["checks"]["piazzone"] = chk_pz

    # 提示（长度要能塞进一行，否则会被窗口右边界切掉）
    add("STATIC", "密码连错 5 次会锁账号 —— 建议勾上「显示密码」先核对。",
        WS_CHILD | WS_VISIBLE | SS_LEFT,
        s(MARGIN), y_hint, s(client_w - MARGIN * 2), s(18), 0, font)

    # 按钮
    add("BUTTON", "保存", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
        s(client_w - MARGIN - 200), y_btn, s(94), s(30), ID_SAVE, font)
    add("BUTTON", "取消", WS_CHILD | WS_VISIBLE | WS_TABSTOP,
        s(client_w - MARGIN - 96), y_btn, s(94), s(30), ID_CANCEL, font)

    def _apply_show(hwnd_):
        """勾「显示密码」→ 取消掩码；去掉 → 恢复掩码（ES_PASSWORD 的掩码靠 EM_SETPASSWORDCHAR）。"""
        on = _USER32.SendMessageW(ctypes.c_void_p(state["checks"]["show"]),
                                 BM_GETCHECK, 0, 0) == BST_CHECKED
        char = 0 if on else ord("●")
        for key in ("password", "password2"):
            h = state["edits"].get(key)
            if h:
                _USER32.SendMessageW(ctypes.c_void_p(h), EM_SETPASSWORDCHAR, char, 0)
                _USER32.InvalidateRect(ctypes.c_void_p(h), None, True)

    def _warn(text: str) -> None:
        _USER32.MessageBoxW(ctypes.c_void_p(state["hwnd"]), text, "还差一点",
                            MB_OK | MB_ICONWARNING | MB_TOPMOST)

    def _do_save(hwnd_):
        base_url = _text(state["edits"]["base_url"]).strip()
        acct_name = _text(state["edits"]["acct_name"]).strip()
        username = _text(state["edits"]["username"]).strip()
        password = _text(state["edits"]["password"])
        if not (username and password):
            _warn("用户名和密码是必填项。")
            return
        if change_key:
            again = _text(state["edits"]["password2"])
            if password != again:
                _warn("两次输入的密码不一样，请核对后再保存。")
                return
        if not (base_url or acct_name):
            _warn("请填服务器地址或公司名。")
            return
        want = _USER32.SendMessageW(ctypes.c_void_p(state["checks"]["piazzone"]),
                                    0x00F0, 0, 0) == BST_CHECKED
        state["done"] = {"base_url": base_url, "acct_name": acct_name,
                         "username": username, "password": password,
                         "want_piazzone": bool(want)}
        _USER32.DestroyWindow(ctypes.c_void_p(hwnd_))

    _USER32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOW)
    _USER32.UpdateWindow(ctypes.c_void_p(hwnd))
    _USER32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_TOPMOST),
                         0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    _USER32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_NOTOPMOST),
                         0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    try:
        _USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        _USER32.SetFocus(ctypes.c_void_p(state["edits"]["base_url"]))
    except Exception:  # noqa: BLE001
        pass

    # ── 消息循环 ──
    msg = MSG()
    while True:
        ret = _USER32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == 0 or ret == -1:
            break
        if msg.message == WM_KEYDOWN and int(msg.wParam) in (VK_RETURN, VK_ESCAPE):
            if not _ime_composing(msg.hwnd):        # 组词中的回车留给输入法
                if int(msg.wParam) == VK_ESCAPE:
                    state["done"] = None
                    _USER32.DestroyWindow(ctypes.c_void_p(hwnd))
                else:
                    _do_save(hwnd)
                continue
        if not _USER32.IsDialogMessageW(ctypes.c_void_p(hwnd), ctypes.byref(msg)):
            _USER32.TranslateMessage(ctypes.byref(msg))
            _USER32.DispatchMessageW(ctypes.byref(msg))

    for h in widgets:
        if h:
            try:
                _USER32.DestroyWindow(ctypes.c_void_p(h))
            except Exception:  # noqa: BLE001
                pass
    for f in (font, font_bold):
        if f:
            _GDI32.DeleteObject(ctypes.c_void_p(f))
    return state["done"]


if __name__ == "__main__":  # 手工自测：直接弹窗
    print(run_form(change_key="--change-key" in sys.argv))
