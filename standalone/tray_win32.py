# -*- coding: utf-8 -*-
"""系統匣圖示 —— 直接呼叫 Windows API,只用 Python 標準庫的 ctypes。

## 為什麼不用現成的套件

原本這裡用的是 pystray,它是 LGPL-3.0 —— 單檔 exe 會把它一起交給使用者,
於是整包就沾上 copyleft 義務(附授權全文、執行時列出聲明、讓使用者能替換
函式庫後重新組合)。本專案是 MIT,不想在散布物上留下這種灰色地帶,因此改
直接呼叫 Windows 的 Shell_NotifyIcon。ctypes 是標準庫,不增加任何相依。

## 這支在做什麼

Windows 的系統匣圖示沒有「圖示物件」這種東西,它是綁在一個視窗上的:
  1. 註冊一個視窗類別,建立一個「從不顯示」的訊息視窗
  2. Shell_NotifyIconW(NIM_ADD) 把圖示掛到系統匣,並指定回呼訊息
  3. 使用者點圖示 → Windows 送回呼訊息給那個隱形視窗 → 我們彈出選單
  4. 跑訊息迴圈直到使用者選「停止並結束」

因此 run() 會佔住呼叫它的執行緒(訊息迴圈的本質),而且必須是建立視窗的
那個執行緒 —— Windows 的訊息佇列是綁執行緒的。
"""
import ctypes
import logging
import os
import sys

log = logging.getLogger("tray")

IS_WINDOWS = os.name == "nt"

# ---- Windows 常數 (值取自 winuser.h / shellapi.h)
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1            # 自訂:系統匣的回呼訊息

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040

MF_STRING, MF_SEPARATOR, MF_DEFAULT = 0x0000, 0x0800, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080

CS_VREDRAW, CS_HREDRAW = 0x0001, 0x0002
IDI_APPLICATION = 32512


def _wire():
    """設定 ctypes 的函式簽章。

    這不是可有可無的禮貌:在 64 位元 Windows 上,指標是 8 bytes 而 ctypes 的
    預設回傳型別是 32 位元 int。少了 restype 設定,HWND 之類的控制代碼會被
    默默截斷成一半 —— 症狀是「有時候好像可以、有時候整個當掉」這種最難查的
    錯誤。全部明寫出來。
    """
    from ctypes import wintypes

    u, k, s = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
    LRESULT = ctypes.c_ssize_t

    u.DefWindowProcW.restype = LRESULT
    u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
    u.CreateWindowExW.restype = wintypes.HWND
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                  wintypes.HINSTANCE, wintypes.LPVOID]
    u.LoadImageW.restype = wintypes.HANDLE
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                             wintypes.UINT, ctypes.c_int, ctypes.c_int,
                             wintypes.UINT]
    u.CreatePopupMenu.restype = wintypes.HMENU
    u.TrackPopupMenu.restype = ctypes.c_int
    u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                 wintypes.LPVOID]
    u.GetMessageW.argtypes = [wintypes.LPVOID, wintypes.HWND,
                              wintypes.UINT, wintypes.UINT]
    s.Shell_NotifyIconW.restype = wintypes.BOOL
    k.GetModuleHandleW.restype = wintypes.HINSTANCE
    k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    return u, k, s, LRESULT


def _notify_icon_struct():
    from ctypes import wintypes

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]
    return NOTIFYICONDATAW


def _wndclass_struct(LRESULT):
    from ctypes import wintypes

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]
    return WNDCLASSW, WNDPROC


def run(icon_path, tooltip, items, on_quit=None):
    """建立系統匣圖示並跑訊息迴圈,直到使用者選了「結束」那一項。

    items: [(文字, 函式 或 None)];None 代表分隔線。第一項會是預設動作
           (粗體,點兩下圖示直接觸發)。最後一項視為「結束」。
    回傳 True = 正常結束;False = 這台機器做不出系統匣 (呼叫端要自己撐著)。
    """
    if not IS_WINDOWS:
        return False
    try:
        u, k, s, LRESULT = _wire()
        NOTIFYICONDATAW = _notify_icon_struct()
        WNDCLASSW, WNDPROC = _wndclass_struct(LRESULT)
    except Exception as e:
        log.warning("Windows API 連接失敗,不建立系統匣: %s", e)
        return False

    from ctypes import wintypes
    quit_requested = []
    handlers = {1000 + i: fn for i, (_, fn) in enumerate(items) if fn}
    quit_id = 1000 + len(items) - 1

    def popup(hwnd):
        menu = u.CreatePopupMenu()
        for i, (text, fn) in enumerate(items):
            if fn is None:
                u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                flags = MF_STRING | (MF_DEFAULT if i == 0 else 0)
                u.AppendMenuW(menu, flags, 1000 + i, text)
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        # 沒有 SetForegroundWindow 的話,選單會在滑鼠移開時卡住不消失 ——
        # 這是 Windows 幾十年的老規矩,Shell_NotifyIcon 的文件明載。
        u.SetForegroundWindow(hwnd)
        cmd = u.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                               pt.x, pt.y, 0, hwnd, None)
        u.DestroyMenu(menu)
        if cmd in handlers:
            handlers[cmd]()
            if cmd == quit_id:
                quit_requested.append(True)
                u.PostQuitMessage(0)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                fn = items[0][1]
                if fn:
                    fn()
            elif lparam == WM_RBUTTONUP:
                popup(hwnd)
            return 0
        if msg == WM_DESTROY:
            u.PostQuitMessage(0)
            return 0
        return u.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = WNDPROC(wndproc)         # 必須留著參照:被 GC 掉 = Windows 回呼到空指標
    cls = WNDCLASSW()
    cls.style = CS_VREDRAW | CS_HREDRAW
    cls.lpfnWndProc = proc
    cls.hInstance = k.GetModuleHandleW(None)
    cls.lpszClassName = "PMSystemTrayWindow"

    try:
        if not u.RegisterClassW(ctypes.byref(cls)):
            raise OSError(f"RegisterClassW 失敗 (error {k.GetLastError()})")
        hwnd = u.CreateWindowExW(0, cls.lpszClassName, "pm-system", 0,
                                 0, 0, 0, 0, None, None, cls.hInstance, None)
        if not hwnd:
            raise OSError(f"CreateWindowExW 失敗 (error {k.GetLastError()})")

        hicon = None
        if icon_path and os.path.exists(icon_path):
            hicon = u.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0,
                                 LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:                # 圖示讀不到也要有圖示,不然系統匣是一片空白
            hicon = u.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = hicon
        nid.szTip = tooltip[:127]
        if not s.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError(f"Shell_NotifyIconW 失敗 (error {k.GetLastError()})")
    except Exception as e:
        log.warning("系統匣建立失敗: %s", e)
        return False

    try:
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
    finally:
        s.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        u.DestroyWindow(hwnd)
        u.UnregisterClassW(cls.lpszClassName, cls.hInstance)
    if on_quit:
        on_quit()
    return True


def selftest():
    """在沒有桌面的機器上也能驗證 ctypes 的接線是否正確 (CI 用)。

    只做「不需要人看得到」的部分:結構大小、類別註冊、建立隱形視窗。
    Shell_NotifyIcon 需要有 Explorer 的工作階段,CI 上會失敗,故不在此檢查。
    """
    if not IS_WINDOWS:
        return "skipped (not Windows)"
    u, k, s, LRESULT = _wire()
    NOTIFYICONDATAW = _notify_icon_struct()
    WNDCLASSW, WNDPROC = _wndclass_struct(LRESULT)
    size = ctypes.sizeof(NOTIFYICONDATAW)
    expect = 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else 956
    if size != expect:
        raise SystemExit(f"NOTIFYICONDATAW 大小是 {size},預期 {expect} —— "
                         "結構定義與這台 Windows 對不上,系統匣會出錯")
    proc = WNDPROC(lambda h, m, w, l: u.DefWindowProcW(h, m, w, l))
    cls = WNDCLASSW()
    cls.lpfnWndProc = proc
    cls.hInstance = k.GetModuleHandleW(None)
    cls.lpszClassName = "PMSystemTraySelftest"
    if not u.RegisterClassW(ctypes.byref(cls)):
        raise SystemExit(f"RegisterClassW 失敗: {k.GetLastError()}")
    hwnd = u.CreateWindowExW(0, cls.lpszClassName, "t", 0, 0, 0, 0, 0,
                            None, None, cls.hInstance, None)
    if not hwnd:
        raise SystemExit(f"CreateWindowExW 失敗: {k.GetLastError()}")
    u.DestroyWindow(hwnd)
    u.UnregisterClassW(cls.lpszClassName, cls.hInstance)
    return f"ok (NOTIFYICONDATAW={size} bytes, {8 * ctypes.sizeof(ctypes.c_void_p)}-bit)"


if __name__ == "__main__":
    print(selftest())
    sys.exit(0)
