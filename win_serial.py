# -*- coding: utf-8 -*-
"""极简 Win32 串口（纯 ctypes，不依赖 pyserial）。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from typing import List, Optional, Tuple

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PURGE_TXCLEAR = 0x0004
PURGE_RXCLEAR = 0x0008
MAXDWORD = 0xFFFFFFFF


class DCB(ctypes.Structure):
    _fields_ = [
        ("DCBlength", wt.DWORD),
        ("BaudRate", wt.DWORD),
        ("fBinary", wt.DWORD, 1),
        ("fParity", wt.DWORD, 1),
        ("fOutxCtsFlow", wt.DWORD, 1),
        ("fOutxDsrFlow", wt.DWORD, 1),
        ("fDtrControl", wt.DWORD, 2),
        ("fDsrSensitivity", wt.DWORD, 1),
        ("fTXContinueOnXoff", wt.DWORD, 1),
        ("fOutX", wt.DWORD, 1),
        ("fInX", wt.DWORD, 1),
        ("fErrorChar", wt.DWORD, 1),
        ("fNull", wt.DWORD, 1),
        ("fRtsControl", wt.DWORD, 2),
        ("fAbortOnError", wt.DWORD, 1),
        ("fDummy2", wt.DWORD, 17),
        ("wReserved", wt.WORD),
        ("XonLim", wt.WORD),
        ("XoffLim", wt.WORD),
        ("ByteSize", wt.BYTE),
        ("Parity", wt.BYTE),
        ("StopBits", wt.BYTE),
        ("XonChar", ctypes.c_char),
        ("XoffChar", ctypes.c_char),
        ("ErrorChar", ctypes.c_char),
        ("EofChar", ctypes.c_char),
        ("EvtChar", ctypes.c_char),
        ("wReserved1", wt.WORD),
    ]


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [
        ("ReadIntervalTimeout", wt.DWORD),
        ("ReadTotalTimeoutMultiplier", wt.DWORD),
        ("ReadTotalTimeoutConstant", wt.DWORD),
        ("WriteTotalTimeoutMultiplier", wt.DWORD),
        ("WriteTotalTimeoutConstant", wt.DWORD),
    ]


kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.GetCommState.argtypes = [wt.HANDLE, ctypes.POINTER(DCB)]
kernel32.SetCommState.argtypes = [wt.HANDLE, ctypes.POINTER(DCB)]
kernel32.SetCommTimeouts.argtypes = [wt.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.PurgeComm.argtypes = [wt.HANDLE, wt.DWORD]


class SerialPort:
    """8N1，无流控，同步读写（带超时）。"""

    def __init__(self) -> None:
        self.handle: Optional[int] = None
        self.port = ""
        self.baud = 0

    @property
    def is_open(self) -> bool:
        return self.handle is not None

    def open(self, port: str, baud: int) -> None:
        self.close()
        path = "\\\\.\\" + port
        handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 0, None,
                                      OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE_VALUE or not handle:
            err = ctypes.get_last_error()
            raise OSError(f"无法打开 {port}（错误码 {err}）。请确认串口未被其他程序占用。")
        self.handle = handle
        self.port = port
        self.baud = baud

        dcb = DCB()
        dcb.DCBlength = ctypes.sizeof(DCB)
        if not kernel32.GetCommState(handle, ctypes.byref(dcb)):
            self.close()
            raise OSError("读取串口参数失败")
        dcb.BaudRate = int(baud)
        dcb.ByteSize = 8
        dcb.Parity = 0       # NOPARITY
        dcb.StopBits = 0     # ONESTOPBIT
        dcb.fBinary = 1
        dcb.fParity = 0
        dcb.fOutxCtsFlow = 0
        dcb.fOutxDsrFlow = 0
        dcb.fDtrControl = 0
        dcb.fDsrSensitivity = 0
        dcb.fOutX = 0
        dcb.fInX = 0
        dcb.fRtsControl = 0
        dcb.fAbortOnError = 0
        if not kernel32.SetCommState(handle, ctypes.byref(dcb)):
            err = ctypes.get_last_error()
            self.close()
            raise OSError(f"设置串口参数失败（{baud} 8N1，错误码 {err}）")

        timeouts = COMMTIMEOUTS()
        timeouts.ReadIntervalTimeout = MAXDWORD
        timeouts.ReadTotalTimeoutMultiplier = MAXDWORD
        timeouts.ReadTotalTimeoutConstant = 100
        timeouts.WriteTotalTimeoutMultiplier = 0
        timeouts.WriteTotalTimeoutConstant = 1000
        kernel32.SetCommTimeouts(handle, ctypes.byref(timeouts))
        kernel32.SetupComm(handle, 4096, 4096)
        kernel32.PurgeComm(handle, PURGE_RXCLEAR | PURGE_TXCLEAR)

    def close(self) -> None:
        if self.handle:
            try:
                kernel32.CloseHandle(self.handle)
            except Exception:
                pass
        self.handle = None

    def write(self, data: bytes) -> int:
        if not self.handle:
            raise OSError("串口未打开")
        buf = ctypes.create_string_buffer(data, len(data))
        written = wt.DWORD(0)
        ok = kernel32.WriteFile(self.handle, buf, len(data), ctypes.byref(written), None)
        if not ok:
            raise OSError(f"串口写入失败（错误码 {ctypes.get_last_error()}）")
        return written.value

    def read(self, max_bytes: int = 512, timeout_ms: int = 30) -> bytes:
        if not self.handle:
            raise OSError("串口未打开")
        timeouts = COMMTIMEOUTS()
        timeouts.ReadIntervalTimeout = MAXDWORD
        timeouts.ReadTotalTimeoutMultiplier = MAXDWORD
        timeouts.ReadTotalTimeoutConstant = max(1, int(timeout_ms))
        timeouts.WriteTotalTimeoutMultiplier = 0
        timeouts.WriteTotalTimeoutConstant = 1000
        kernel32.SetCommTimeouts(self.handle, ctypes.byref(timeouts))
        buf = ctypes.create_string_buffer(max_bytes)
        n = wt.DWORD(0)
        ok = kernel32.ReadFile(self.handle, buf, max_bytes, ctypes.byref(n), None)
        if not ok:
            raise OSError(f"串口读取失败（错误码 {ctypes.get_last_error()}）")
        return buf.raw[:n.value]


def list_ports() -> List[str]:
    """从注册表枚举当前可用串口。"""
    import winreg
    ports: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            i = 0
            while True:
                try:
                    _name, value, _typ = winreg.EnumValue(key, i)
                except OSError:
                    break
                ports.append(str(value))
                i += 1
    except OSError:
        pass
    def sort_key(p: str):
        digits = "".join(ch for ch in p if ch.isdigit())
        return (int(digits) if digits else 999, p)
    return sorted(set(ports), key=sort_key)


if __name__ == "__main__":
    print("ports:", list_ports())
