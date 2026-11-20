# -*- coding: utf-8 -*-
"""极简 Windows 托盘图标（纯 ctypes，无第三方依赖）。

用法::

    tray = TrayIcon("电池实时监控", "app.ico", on_command)
    tray.start()      # 后台线程，创建隐藏窗口 + 托盘图标
    tray.notify("标题", "内容")
    tray.stop()

on_command(cmd) 在托盘线程里被调用，cmd 为 "show" / "open_dir" / "exit"，
调用方需要自行转投到主线程（例如放进 queue 由 Tk after 轮询）。
"""

from __future__ import annotations

import ctypes
import threading
import traceback
from ctypes import wintypes
from typing import Callable, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_TRAY_NOTIFY = WM_APP + 2

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
SM_CXSMICON = 49
SM_CYSMICON = 50

ID_SHOW = 1001
ID_OPEN_DIR = 1002
ID_EXIT = 1003

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
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


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


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
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", POINT)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.restype = wintypes.UINT


class TrayIcon(threading.Thread):
    def __init__(self, tooltip: str, icon_path: str,
                 on_command: Callable[[str], None], class_name: str = "BmsMonitorTray"):
        super().__init__(daemon=True)
        self.tooltip = tooltip
        self.icon_path = icon_path
        self.on_command = on_command
        self.class_name = class_name
        self.hwnd: Optional[int] = None
        self.hicon = None
        self.ready = threading.Event()
        self.ok = False
        self._pending_notify: Optional[tuple] = None
        self._wndproc_ref = WNDPROC(self._wndproc)

    # -- 线程主体 ---------------------------------------------------------- #
    def run(self) -> None:
        try:
            self._create()
            self.ok = True
            self.ready.set()
            self._loop()
        except Exception:
            traceback.print_exc()
            self.ready.set()
        finally:
            self._remove_icon()
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None

    def _create(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_ref
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = 0
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszMenuName = None
        wc.lpszClassName = self.class_name
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            0, self.class_name, "BMS Monitor Tray", 0,
            0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise RuntimeError(f"CreateWindowExW failed: {ctypes.get_last_error()}")

        cx = user32.GetSystemMetrics(SM_CXSMICON)
        cy = user32.GetSystemMetrics(SM_CYSMICON)
        if self.icon_path:
            self.hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON,
                                           max(cx, 16), max(cy, 16),
                                           LR_LOADFROMFILE)
        if not self.hicon:
            self.hicon = user32.LoadIconW(None, ctypes.c_wchar_p(32512))  # IDI_APPLICATION
        self._add_icon()

    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip[:127]
        return nid

    def _add_icon(self) -> None:
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _remove_icon(self) -> None:
        if self.hwnd:
            nid = self._nid(0)
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _loop(self) -> None:
        msg = MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # -- 消息处理 ---------------------------------------------------------- #
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                event = int(lparam) & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._safe_command("show")
                elif event == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_TRAY_NOTIFY:
                self._show_balloon()
                return 0
            if msg == WM_CLOSE:
                user32.PostQuitMessage(0)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            traceback.print_exc()
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self) -> None:
        menu = user32.CreatePopupMenu()
        try:
            user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "打开监控界面")
            user32.AppendMenuW(menu, MF_STRING, ID_OPEN_DIR, "打开记录文件夹")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "退出监控")
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | 0x0100,
                pt.x, pt.y, 0, self.hwnd, None)
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd == ID_SHOW:
                self._safe_command("show")
            elif cmd == ID_OPEN_DIR:
                self._safe_command("open_dir")
            elif cmd == ID_EXIT:
                self._safe_command("exit")
        finally:
            user32.DestroyMenu(menu)

    def _safe_command(self, cmd: str) -> None:
        try:
            self.on_command(cmd)
        except Exception:
            traceback.print_exc()

    def _show_balloon(self) -> None:
        data = self._pending_notify
        self._pending_notify = None
        if not data:
            return
        title, text = data
        nid = self._nid(NIF_INFO | NIF_TIP | NIF_ICON)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # -- 对外接口 ---------------------------------------------------------- #
    def notify(self, title: str, text: str) -> None:
        self._pending_notify = (title, text)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_TRAY_NOTIFY, 0, 0)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self.join(timeout=1.5)
