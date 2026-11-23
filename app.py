# -*- coding: utf-8 -*-
"""电池老化测试上位机（精简版）

只保留老化测试需要的：
    * 6 节电芯电压、最高/最低/压差
    * THM0=MOS 温度、THM1=插座温度、THM2=电芯温度
    * 循环次数、均衡状态、SOC、MOS 状态
    * 串口选择、波特率选择、连接状态
    * 每一帧写入 CSV，每 30 分钟一个文件
    * 关闭窗口即退出程序（不后台驻留）

协议与 BmsStudioV3Qt 3.8.13 Qt030 一致（0x7B/0x7D + CRC8）。
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import queue
import sys
import threading
import time
import traceback
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import bms_protocol as proto
from win_serial import SerialPort, list_ports
import updater

APP_VERSION = "2026.11.24.2"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BMS_DATA_DIR") or APP_DIR)
CONFIG_PATH = Path(os.environ.get("BMS_CONFIG_PATH") or (DATA_DIR / "config.json"))
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "port": "",
    "baud": 19200,
    "cell_count": 6,
    "cycle_s": 2.0,
    "timeout_ms": 800,
    "record_window_min": 30,
    "output_dir": "记录",
    "update": {
        "manifest_url": "",
        "timeout_s": 15,
        "proxy": "",
    },
    "alarms": {
        "cell_max_v": 4.3,
        "cell_min_v": 2.5,
        "delta_mv": 200,
        "temp_c": 75,
        "temp_warn_c": 65,
        "temp_delta_warn_c": 20,
        "beep": True,
    },
}

BAUDS = [9600, 19200, 38400, 57600, 115200]


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    src = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    if src.exists():
        try:
            with open(src, "r", encoding="utf-8-sig") as f:
                cfg = deep_merge(cfg, json.load(f))
        except Exception as exc:
            print(f"[警告] 读取 config.json 失败: {exc}")
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def local_now() -> datetime:
    return datetime.now().astimezone()


def fmt_dt(dt: Optional[datetime] = None) -> str:
    return (dt or local_now()).strftime("%Y-%m-%d %H:%M:%S")


def fmt_hms(dt: Optional[datetime] = None) -> str:
    return (dt or local_now()).strftime("%H:%M:%S")


def valid_temp(v: Optional[float]) -> bool:
    return v is not None and -40.0 <= v <= 150.0


def valid_cell(mv: Optional[int]) -> bool:
    return mv is not None and 0 <= mv <= 5000


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #

@dataclass
class BmsData:
    cells: List[int] = field(default_factory=list)
    cell_temp: Optional[float] = None      # THM2 电芯
    mos_temp: Optional[float] = None       # THM0 MOS
    socket_temp: Optional[float] = None    # THM1 插座/正极
    cycle_count: Optional[int] = None
    cell_count: Optional[int] = None
    charge_mos: Optional[bool] = None
    discharge_mos: Optional[bool] = None
    balance: List[bool] = field(default_factory=list)
    charging: Optional[bool] = None
    discharging: Optional[bool] = None
    total_v: Optional[float] = None
    current_a: Optional[float] = None
    soc: Optional[float] = None
    soh: Optional[float] = None
    alarm: bool = False
    updated: Optional[datetime] = None

    @property
    def temp_delta(self) -> Optional[float]:
        vals = [v for v in (self.mos_temp, self.socket_temp, self.cell_temp) if v is not None]
        if len(vals) < 2:
            return None
        return round(max(vals) - min(vals), 1)

    def copy(self) -> "BmsData":
        return BmsData(
            cells=list(self.cells), cell_temp=self.cell_temp, mos_temp=self.mos_temp,
            socket_temp=self.socket_temp, cycle_count=self.cycle_count,
            cell_count=self.cell_count, charge_mos=self.charge_mos,
            discharge_mos=self.discharge_mos, charging=self.charging,
            discharging=self.discharging, total_v=self.total_v, current_a=self.current_a,
            soc=self.soc, soh=self.soh, alarm=self.alarm, updated=self.updated,
            balance=list(self.balance),
        )


def charge_text(d: BmsData) -> str:
    if d.current_a is not None:
        if d.current_a < -0.1:
            return "放电"
        if d.current_a > 0.1:
            return "充电"
        return "静置"
    if d.charging:
        return "充电"
    if d.discharging:
        return "放电"
    return "--"


# --------------------------------------------------------------------------- #
# 读取线程
# --------------------------------------------------------------------------- #

class BaseReader(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.data = BmsData()
        self.status = "未连接"
        self.detail = ""
        self.last_rx = 0.0
        self.frames_ok = 0
        self.timeouts = 0
        self.error = ""
        self.connected = False

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            d = self.data.copy()
            return {
                "data": d,
                "status": self.status,
                "detail": self.detail,
                "last_rx": self.last_rx,
                "frames": self.frames_ok,
                "timeouts": self.timeouts,
                "connected": self.connected,
            }

    def stop(self) -> None:
        self.stop_event.set()

    def _set_status(self, status: str, detail: str = "") -> None:
        with self.lock:
            self.status = status
            self.detail = detail


class BmsReader(BaseReader):
    def __init__(self, port: str, baud: int, cfg: Dict[str, Any]):
        super().__init__()
        self.port = port
        self.baud = baud
        self.cfg = cfg
        self.ser = SerialPort()
        self.parser = proto.FrameParser()
        self.timeout_s = max(0.2, float(cfg.get("timeout_ms", 800)) / 1000.0)
        self.cycle_s = max(1.0, float(cfg.get("cycle_s", 2.0)))
        self.cell_count = int(cfg.get("cell_count", 6) or 6)
        self._last_sys = 0.0
        self._fail_streak = 0
        self._got_any = False

    # -- 主循环 ------------------------------------------------------------ #
    def run(self) -> None:
        try:
            self._set_status("连接中", f"{self.port} {self.baud} 8N1")
            self.ser.open(self.port, self.baud)
            self.connected = True
            self._set_status("已连接", f"{self.port} {self.baud} 8N1")

            if self._query(0x20, timeout=2.0) is None:
                raise OSError("设备无应答：0x20 握手超时")
            self._query(0x33, timeout=1.2)
            self._last_sys = time.monotonic()

            while not self.stop_event.is_set():
                start = time.monotonic()
                for cmd in (0x30, 0x31, 0x32, 0x50):
                    if self.stop_event.is_set():
                        break
                    self._query(cmd)
                if time.monotonic() - self._last_sys >= 30.0:
                    self._query(0x33)
                    self._last_sys = time.monotonic()

                if self._fail_streak >= 8:
                    raise OSError("连续无应答，已断开")

                wait = self.cycle_s - (time.monotonic() - start)
                if wait > 0:
                    self.stop_event.wait(wait)
        except Exception as exc:
            self.error = str(exc)
            self.connected = False
            self._set_status("连接失败" if not self._got_any else "已断开", str(exc))
        finally:
            try:
                self.ser.close()
            except Exception:
                pass
            self.connected = False

    # -- 单条查询 ---------------------------------------------------------- #
    def _query(self, cmd: int, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if self.stop_event.is_set():
            return None
        try:
            self.ser.write(proto.build_request(cmd))
        except Exception as exc:
            self.timeouts += 1
            self._fail_streak += 1
            raise
        deadline = time.monotonic() + (timeout or self.timeout_s)
        got: Optional[Dict[str, Any]] = None
        while time.monotonic() < deadline:
            chunk = self.ser.read(256, 20)
            if chunk:
                for fcmd, payload, raw in self.parser.feed(chunk):
                    decoded = proto.decode_payload(fcmd, payload)
                    if decoded is None:
                        continue
                    self.frames_ok += 1
                    self.last_rx = time.time()
                    self._got_any = True
                    with self.lock:
                        self._apply(fcmd, decoded)
                    if fcmd == cmd:
                        got = decoded
            if got is not None:
                break
        if got is None:
            self.timeouts += 1
            self._fail_streak += 1
        else:
            self._fail_streak = 0
            self._set_status("已连接", f"最后数据 {fmt_hms()}")
        return got

    # -- 组装显示数据 ------------------------------------------------------ #
    def _apply(self, cmd: int, decoded: Dict[str, Any]) -> None:
        d = self.data
        if cmd == 0x30:
            cells = [c for c in decoded.get("cells_mV", []) if valid_cell(c)]
            if cells:
                n = self.cell_count if 0 < self.cell_count <= len(cells) else len(cells)
                d.cells = cells[:n]
        elif cmd == 0x31:
            t = decoded.get("cell_temp_C")
            if valid_temp(t):
                d.cell_temp = t
        elif cmd == 0x32:
            if valid_temp(decoded.get("mos_temp_C")):
                d.mos_temp = decoded["mos_temp_C"]
            if valid_temp(decoded.get("socket_temp_C")):
                d.socket_temp = decoded["socket_temp_C"]
        elif cmd == 0x33:
            cc = decoded.get("cell_count")
            if cc and 0 < cc <= 32:
                self.cell_count = int(cc)
                d.cell_count = int(cc)
            if decoded.get("cycle_count") is not None:
                d.cycle_count = int(decoded["cycle_count"])
        elif cmd == 0x50:
            tv = decoded.get("total_voltage_V")
            d.total_v = tv if tv is not None else decoded.get("pack_voltage_V")
            d.current_a = decoded.get("current_A")
            d.soc = decoded.get("soc_pct")
            d.soh = decoded.get("soh_pct")
            d.charging = decoded.get("charging")
            d.discharging = decoded.get("discharging")
            d.charge_mos = decoded.get("charge_mos")
            d.discharge_mos = decoded.get("discharge_mos")
            d.balance = list(decoded.get("balance") or [])
            d.alarm = bool(decoded.get("alarm_active"))
        if d.updated is None or True:
            d.updated = local_now()

class DemoReader(BaseReader):
    """没有硬件时验证界面/记录用。"""

    def __init__(self, cfg: Dict[str, Any]):
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:
        self.connected = True
        self._set_status("已连接", "模拟数据")
        n = int(self.cfg.get("cell_count", 6) or 6)
        base = [3.85, 3.84, 3.86, 3.85, 3.84, 3.83]
        cycle = 128
        i = 0
        while not self.stop_event.is_set():
            i += 1
            phase = (i // 12) % 5
            with self.lock:
                d = self.data
                d.cells = [int((base[j] + (0.001 if j == 0 else 0) + 0.0005 * (i % 7)) * 1000)
                           for j in range(n)]
                if phase == 1:
                    d.cells[0] = 4320          # 单体过压
                elif phase == 2:
                    d.cells[-1] = 2450         # 单体欠压
                else:
                    d.cells[-1] = d.cells[-1] - 12
                if phase == 3:
                    d.mos_temp = 70.0          # 超过 65℃，字体变红（预警，不告警）
                elif phase == 4:
                    d.mos_temp = 78.0          # 超过 75℃，进入告警
                else:
                    d.mos_temp = 36.0 + (i % 5)
                d.socket_temp = 38.5 + (i % 4)
                d.cell_temp = 31.0 + (i % 3)
                d.cycle_count = cycle + i // 60
                d.charge_mos = True
                d.discharge_mos = True
                d.balance = ([True, False, True, False, False, False] if phase == 1 else
                             [False, True, False, True, False, True] if phase == 2 else
                             [False] * 6)
                d.charging = (i // 90) % 2 == 0
                d.discharging = not d.charging
                d.total_v = round(sum(d.cells) / 1000.0, 3)
                d.current_a = -11.8 if phase == 3 else (12.4 if d.charging else -11.8)
                d.soc = 8.0 if phase == 4 else (25.0 if phase == 2 else (85.0 if phase == 1 else 63.0))
                d.soh = 98.0
                d.updated = local_now()
                self.last_rx = time.time()
                self.frames_ok += 1
            self.stop_event.wait(1.0)


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

DETAIL_HEADER = [
    "时间", "充放电状态", "电流(A)", "SOC(%)", "循环次数",
    "THM0_MOS(℃)", "THM1_插座(℃)", "THM2_电芯(℃)", "温差(℃)",
    "电芯1(V)", "电芯2(V)", "电芯3(V)", "电芯4(V)", "电芯5(V)", "电芯6(V)",
    "最高单体(V)", "最低单体(V)", "压差(mV)",
    "报警状态", "报警类型",
]


class DailyCsv:
    def __init__(self, folder: Path, prefix: str = "老化记录", header: Optional[List[str]] = None):
        self.folder = folder
        self.prefix = prefix
        self.header = header or DETAIL_HEADER
        self.day = ""
        self.path: Optional[Path] = None
        self._fh = None

    def _ensure(self) -> None:
        day = local_now().strftime("%Y%m%d")
        if day == self.day and self._fh:
            return
        self.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.day = day
        self.path = self.folder / f"{self.prefix}_{day}.csv"
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = open(self.path, "a", encoding="utf-8-sig", newline="")
        if new:
            csv.writer(self._fh).writerow(self.header)
            self._fh.flush()

    def write(self, row: List[Any]) -> None:
        self._ensure()
        csv.writer(self._fh).writerow(row)
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None


class WindowCsv:
    """每 30 分钟一个文件，逐帧记录。"""

    def __init__(self, folder: Path, minutes: int = 30, prefix: str = "老化记录",
                 header: Optional[List[str]] = None):
        self.folder = folder
        self.minutes = max(1, int(minutes))
        self.prefix = prefix
        self.header = header or DETAIL_HEADER
        self.window: Optional[datetime] = None
        self.path: Optional[Path] = None
        self.rows = 0
        self._fh = None

    def _window_start(self, dt: datetime) -> datetime:
        minute = (dt.minute // self.minutes) * self.minutes
        return dt.replace(minute=minute, second=0, microsecond=0)

    def _open_window(self) -> None:
        path = self.path
        new = not path.exists() or path.stat().st_size == 0
        if not new:
            first = ""
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    first = f.readline().rstrip("\r\n")
            except Exception:
                first = ""
            try:
                old_fields = next(csv.reader([first])) if first else []
            except Exception:
                old_fields = []
            if old_fields != [str(h) for h in self.header]:
                # 旧版本列格式：改名保留原数据，新文件用新表头
                n = 1
                while True:
                    old = path.with_name(f"{path.stem}_旧格式{n}{path.suffix}")
                    if not old.exists():
                        break
                    n += 1
                try:
                    path.rename(old)
                except Exception:
                    pass
                new = True
        self._fh = open(path, "a", encoding="utf-8-sig", newline="")
        if new:
            csv.writer(self._fh).writerow(self.header)
        self._fh.flush()

    def write(self, dt: datetime, row: List[Any]) -> None:
        start = self._window_start(dt)
        if start != self.window or self._fh is None:
            self.close()
            self.window = start
            self.folder.mkdir(parents=True, exist_ok=True)
            self.path = self.folder / f"{self.prefix}_{start:%Y%m%d_%H%M}.csv"
            self._open_window()
            self.rows = 0
        csv.writer(self._fh).writerow(row)
        self._fh.flush()
        self.rows += 1

    def close(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None


def detail_row(d: BmsData, alarm_text: str = "") -> List[Any]:
    cells = list(d.cells) + [""] * (6 - len(d.cells))
    cells = [f"{v / 1000:.3f}" if isinstance(v, (int, float)) else "" for v in cells[:6]]
    names = (alarm_text or "").strip()
    vmax = f"{max(d.cells) / 1000:.3f}" if d.cells else ""
    vmin = f"{min(d.cells) / 1000:.3f}" if d.cells else ""
    delta = f"{max(d.cells) - min(d.cells)}" if d.cells else ""
    return [
        fmt_dt(d.updated), charge_text(d),
        f"{d.current_a:.1f}" if d.current_a is not None else "",
        f"{d.soc:.0f}" if d.soc is not None else "",
        str(d.cycle_count) if d.cycle_count is not None else "",
        f"{d.mos_temp:.1f}" if d.mos_temp is not None else "",
        f"{d.socket_temp:.1f}" if d.socket_temp is not None else "",
        f"{d.cell_temp:.1f}" if d.cell_temp is not None else "",
        f"{d.temp_delta:.1f}" if d.temp_delta is not None else "",
        *cells,
        vmax, vmin, delta,
        "报警" if names else "正常",
        names if names else "无",
    ]


def soc_ring_png(value: Optional[float], size: int, ring: int,
                 prog_rgb: tuple, track_rgb: tuple = (209, 213, 219)) -> str:
    """生成带抗锯齿的圆环 PNG（透明背景），返回 base64 字符串。"""
    import base64
    import math
    import struct
    import zlib

    size = max(40, int(size))
    ring = max(4, int(ring))
    c = size / 2.0
    radius = (size - ring) / 2.0 - 1.0
    half = ring / 2.0
    extent = 0.0 if value is None else max(0.0, min(100.0, value)) * 3.6
    rows = []
    for y in range(size):
        row = bytearray()
        dy = y + 0.5 - c
        for x in range(size):
            dx = x + 0.5 - c
            d = math.hypot(dx, dy)
            cov = half + 0.5 - abs(d - radius)
            if cov <= 0.0:
                row += b"\x00\x00\x00\x00"
                continue
            cov = 1.0 if cov > 1.0 else cov
            if extent <= 0.0:
                cp = 0.0
            elif extent >= 359.999:
                cp = 1.0
            else:
                ang = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
                # 起点 0° 处做 1.6° 的抗锯齿过渡，其余角度都属于进度弧
                start_cov = min(1.0, ang / 1.6 + 0.5)
                end_cov = min(1.0, max(0.0, (extent - ang) / 1.6 + 0.5))
                cp = min(start_cov, end_cov)
            r = track_rgb[0] + (prog_rgb[0] - track_rgb[0]) * cp
            g = track_rgb[1] + (prog_rgb[1] - track_rgb[1]) * cp
            b = track_rgb[2] + (prog_rgb[2] - track_rgb[2]) * cp
            a = int(round(cov * 255))
            row += bytes((int(round(r)), int(round(g)), int(round(b)), a))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


# --------------------------------------------------------------------------- #
# 告警
# --------------------------------------------------------------------------- #

ALARM_HEADER = ["时间", "告警类型", "状态", "触发值", "阈值", "详细信息", "持续秒"]


@dataclass
class AlarmCond:
    key: str
    name: str
    limit: float
    unit: str
    active: bool = False
    since: Optional[datetime] = None
    peak: float = 0.0
    count: int = 0


class AlarmBook:
    """4 个阈值：单体过压 / 单体欠压 / 压差过大 / 温度过高。

    只在“状态变化”时记录：每次从正常变成超限算 1 条告警。
    """

    def __init__(self, cfg: Dict[str, Any], writer: DailyCsv):
        a = cfg.get("alarms") or {}
        self.writer = writer
        self.conds = [
            AlarmCond("vmax", "单体过压", float(a.get("cell_max_v", 4.3)), "V"),
            AlarmCond("vmin", "单体欠压", float(a.get("cell_min_v", 2.5)), "V"),
            AlarmCond("delta", "压差过大", float(a.get("delta_mv", 200)), "mV"),
            AlarmCond("temp", "温度过高", float(a.get("temp_c", 75)), "℃"),
        ]
        self.total = 0

    def reset(self) -> None:
        for c in self.conds:
            c.active = False
            c.since = None
            c.peak = 0.0
            c.count = 0
        self.total = 0

    def _values(self, d: BmsData):
        vmax = max(d.cells) / 1000.0 if d.cells else None
        vmin = min(d.cells) / 1000.0 if d.cells else None
        imax = d.cells.index(max(d.cells)) if d.cells else -1
        imin = d.cells.index(min(d.cells)) if d.cells else -1
        delta = (max(d.cells) - min(d.cells)) if len(d.cells) >= 2 else None
        temps = []
        if d.mos_temp is not None:
            temps.append(("THM0 MOS", d.mos_temp))
        if d.socket_temp is not None:
            temps.append(("THM1 插座", d.socket_temp))
        if d.cell_temp is not None:
            temps.append(("THM2 电芯", d.cell_temp))
        tmax = max(temps, key=lambda x: x[1]) if temps else None
        return {
            "vmax": vmax, "vmin": vmin, "delta": delta,
            "temp": tmax[1] if tmax else None,
            "imax": imax, "imin": imin, "tmax_label": tmax[0] if tmax else "",
        }

    def _detail(self, key: str, v: Dict[str, Any]) -> str:
        if key == "vmax":
            return f"最高第 {v['imax'] + 1} 节 {v['vmax']:.3f} V"
        if key == "vmin":
            return f"最低第 {v['imin'] + 1} 节 {v['vmin']:.3f} V"
        if key == "delta":
            return f"最高第 {v['imax'] + 1} 节 / 最低第 {v['imin'] + 1} 节，压差 {v['delta']:.0f} mV"
        return f"{v['tmax_label']} {v['temp']:.1f} ℃"

    def evaluate(self, d: BmsData, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or local_now()
        v = self._values(d)
        events: List[Dict[str, Any]] = []
        for c in self.conds:
            value = v.get(c.key)
            if value is None:
                continue
            breached = value < c.limit if c.key == "vmin" else value > c.limit
            detail = self._detail(c.key, v)
            if breached and not c.active:
                c.active = True
                c.since = now
                c.peak = value
                c.count += 1
                self.total += 1
                self._write(now, c, "触发", value, detail, "")
                events.append({"kind": "触发", "cond": c, "value": value, "detail": detail})
            elif breached and c.active:
                c.peak = min(c.peak, value) if c.key == "vmin" else max(c.peak, value)
            elif not breached and c.active:
                c.active = False
                dur = int((now - c.since).total_seconds()) if c.since else 0
                self._write(now, c, "恢复", value, detail, dur)
                events.append({"kind": "恢复", "cond": c, "value": value, "detail": detail})
        return events

    def _write(self, now: datetime, c: AlarmCond, state: str, value: float,
               detail: str, duration: Any) -> None:
        try:
            self.writer.write([
                fmt_dt(now), c.name, state,
                f"{value:.3f}" if c.unit == "V" else f"{value:.0f}" if c.unit == "mV" else f"{value:.1f}",
                f"{c.limit:g} {c.unit}", detail, duration,
            ])
        except Exception:
            pass

    def active_text(self) -> str:
        act = [c.name for c in self.conds if c.active]
        return "、".join(act)



BG = "#ffffff"
CARD = "#f8fafc"
CARD2 = "#f1f5f9"
FG = "#111827"
MUTED = "#6b7280"
GREEN = "#059669"
YELLOW = "#d97706"
RED = "#dc2626"
BLUE = "#2563eb"
DARK_BLUE = "#1e40af"
LABEL_FG = "#374151"

FONT_UI = "宋体"
FONT_NUM = "宋体"


def run_gui(cfg: Dict[str, Any], demo: bool = False) -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    import tkinter as tk
    from tkinter import ttk, messagebox
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title("电池老化测试上位机 · 6S")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    win_w = min(1180, max(980, sw - 80))
    win_h = min(960, max(640, sh - 100))
    root.geometry(f"{win_w}x{win_h}+{(sw - win_w) // 2}+{max(10, (sh - win_h) // 2)}")
    root.minsize(1000, 700)
    root.resizable(True, True)
    root.configure(bg=BG)
    icon = APP_DIR / "app.ico"
    if icon.exists():
        try:
            root.iconbitmap(default=str(icon))
        except Exception:
            pass

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG, font=(FONT_UI, 10))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT_UI, 9))
    style.configure("TButton", font=(FONT_UI, 9), background="#e5e7eb",
                    foreground=FG, borderwidth=0, focusthickness=0, padding=(14, 6))
    style.map("TButton", background=[("active", "#d1d5db")])
    style.configure("TCombobox", fieldbackground="#f1f5f9", background="#e5e7eb",
                    foreground=FG, arrowcolor=FG, padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", "#f1f5f9"), ("disabled", "#f1f5f9")],
              foreground=[("readonly", FG)],
              selectbackground=[("readonly", "#f1f5f9")],
              selectforeground=[("readonly", FG)],
              background=[("readonly", "#e5e7eb"), ("active", "#d1d5db")])
    root.option_add("*TCombobox*Listbox.background", "#f1f5f9")
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", "#d1d5db")
    style.configure("TSeparator", background="#e5e7eb")

    out_dir = Path(cfg.get("output_dir") or "记录")
    if not out_dir.is_absolute():
        out_dir = DATA_DIR / out_dir
    window_min = int(cfg.get("record_window_min", 30) or 30)
    detail_csv = WindowCsv(out_dir, window_min)
    alarm_csv = DailyCsv(out_dir, "告警记录", ALARM_HEADER)
    alarm_book = AlarmBook(cfg, alarm_csv)
    last_alarm_eval: Optional[datetime] = None
    beep_on = bool((cfg.get("alarms") or {}).get("beep", True))
    ath = cfg.get("alarms") or {}
    record_count = 0
    last_record_updated: Optional[datetime] = None

    def beep() -> None:
        if not beep_on:
            return

        def _run() -> None:
            try:
                import winsound
                winsound.Beep(1500, 250)
                winsound.Beep(1900, 250)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    upd_q: "queue.Queue[tuple]" = queue.Queue()
    update_state = {"busy": False}
    reader: Optional[BaseReader] = None
    stop_flag = threading.Event()

    def make_reader() -> BaseReader:
        if demo:
            return DemoReader(cfg)
        port = port_var.get().strip()
        baud = int(baud_var.get())
        if not port:
            raise ValueError("请选择串口")
        return BmsReader(port, baud, cfg)

    def start_reader() -> None:
        nonlocal reader, last_alarm_eval, record_count, last_record_updated
        if reader is not None and reader.is_alive():
            return
        try:
            reader = make_reader()
        except Exception as exc:
            messagebox.showerror("无法连接", str(exc))
            return
        last_alarm_eval = None
        last_record_updated = None
        record_count = 0
        alarm_book.reset()
        update_alarm_bar()
        cfg["port"] = port_var.get().strip()
        cfg["baud"] = int(baud_var.get())
        if not demo:
            save_config(cfg)
        reader.start()
        connect_btn.config(text="断开")
        refresh_ports()

    def stop_reader() -> None:
        nonlocal reader
        if reader is not None:
            reader.stop()
            reader = None
        connect_btn.config(text="连接")

    def toggle_connect() -> None:
        if reader is not None and reader.is_alive():
            stop_reader()
            set_status("未连接", "")
        else:
            start_reader()

    # ---- 顶部 ------------------------------------------------------------ #
    top = tk.Frame(root, bg=CARD, height=58, highlightthickness=1,
                   highlightbackground="#e5e7eb")
    top.grid(row=0, column=0, sticky="ew")
    top.pack_propagate(False)

    def make_option(parent, var, values, width=10):
        om = tk.OptionMenu(parent, var, *(values or [""]))
        om.config(bg=CARD2, fg=FG, activebackground="#d1d5db", activeforeground=FG,
                  highlightthickness=0, bd=0, relief="flat", font=(FONT_NUM, 10),
                  width=width, anchor="w", padx=6, pady=4, indicatoron=0)
        om["menu"].config(bg=CARD2, fg=FG, activebackground="#d1d5db",
                          activeforeground=FG, font=(FONT_NUM, 10), bd=0)
        return om

    tk.Label(top, text="串口", bg=CARD, fg=LABEL_FG, font=(FONT_UI, 10)).pack(side="left", padx=(16, 4))
    port_var = tk.StringVar(value=str(cfg.get("port") or ""))
    port_box = make_option(top, port_var, list_ports(), width=8)
    port_box.pack(side="left", padx=(0, 2), pady=12)
    tk.Label(top, text="▾", bg=CARD, fg=MUTED, font=(FONT_UI, 9)).pack(side="left", padx=(0, 10))

    tk.Label(top, text="波特率", bg=CARD, fg=LABEL_FG, font=(FONT_UI, 10)).pack(side="left", padx=(0, 4))
    baud_var = tk.StringVar(value=str(cfg.get("baud", 19200)))
    baud_box = make_option(top, baud_var, [str(b) for b in BAUDS], width=7)
    baud_box.pack(side="left", padx=(0, 2), pady=12)
    tk.Label(top, text="▾", bg=CARD, fg=MUTED, font=(FONT_UI, 9)).pack(side="left", padx=(0, 10))

    connect_btn = ttk.Button(top, text="连接", command=toggle_connect)
    connect_btn.pack(side="left", padx=(0, 12))

    tk.Button(top, text="刷新", bg=CARD, fg=FG, bd=0, activebackground=CARD,
              font=(FONT_UI, 9), command=lambda: refresh_ports()).pack(side="left")

    status_dot = tk.Label(top, text="●", bg=CARD, fg=YELLOW, font=(FONT_UI, 14))
    status_dot.pack(side="right", padx=(0, 4))
    status_lbl = tk.Label(top, text="未连接", bg=CARD, fg=FG, font=(FONT_UI, 11, "bold"))
    status_lbl.pack(side="right", padx=(0, 10))
    detail_lbl = tk.Label(top, text="", bg=CARD, fg=MUTED, font=(FONT_UI, 9))
    detail_lbl.pack(side="right", padx=(0, 14))

    def refresh_ports() -> None:
        ports = list_ports()
        menu = port_box["menu"]
        menu.delete(0, "end")
        for p in ports or [""]:
            menu.add_command(label=p, command=lambda v=p: port_var.set(v))
        if port_var.get() not in ports and ports:
            port_var.set(ports[0])

    def set_status(text: str, detail: str = "") -> None:
        status_lbl.config(text=text)
        detail_lbl.config(text=detail)

    # ---- 告警条 ---------------------------------------------------------- #
    alarm_bar = tk.Frame(root, bg=CARD, height=46)
    alarm_bar.grid(row=1, column=0, sticky="ew")
    alarm_bar.pack_propagate(False)

    alarm_dot = tk.Label(alarm_bar, text="●", bg=CARD, fg=GREEN, font=(FONT_UI, 13))
    alarm_dot.pack(side="left", padx=(16, 5))
    alarm_lbl = tk.Label(alarm_bar, text="运行正常，暂无告警", bg=CARD, fg=FG,
                         font=(FONT_UI, 10, "bold"))
    alarm_lbl.pack(side="left")
    alarm_count = tk.Label(alarm_bar, text="累计告警 0 次", bg=CARD, fg=MUTED,
                           font=(FONT_NUM, 11, "bold"))
    alarm_count.pack(side="right", padx=16)
    alarm_chips: Dict[str, tk.Label] = {}
    for key, name in (("temp", "过温"), ("delta", "压差"), ("vmin", "欠压"), ("vmax", "过压")):
        lbl = tk.Label(alarm_bar, text=f"{name} 0", bg=CARD, fg=MUTED,
                       font=(FONT_UI, 9))
        lbl.pack(side="right", padx=7)
        alarm_chips[key] = lbl

    alarm_names = {"vmax": "过压", "vmin": "欠压", "delta": "压差", "temp": "过温"}

    def update_alarm_bar() -> None:
        act = [c for c in alarm_book.conds if c.active]
        if act:
            alarm_bar.config(bg="#fee2e2")
            alarm_dot.config(fg=RED)
            parts = []
            for c in act:
                v = f"{c.peak:.3f}V" if c.unit == "V" else (f"{c.peak:.0f}mV" if c.unit == "mV" else f"{c.peak:.1f}℃")
                parts.append(f"{c.name} {v}")
            alarm_lbl.config(text="⚠ " + "；".join(parts), fg="#b91c1c")
        else:
            alarm_bar.config(bg=CARD)
            alarm_dot.config(fg=GREEN)
            alarm_lbl.config(text="运行正常，暂无告警", fg=FG)
        alarm_count.config(text=f"累计告警 {alarm_book.total} 次",
                           fg=RED if alarm_book.total else MUTED)
        for c in alarm_book.conds:
            alarm_chips[c.key].config(
                text=f"{alarm_names[c.key]} {c.count}",
                fg=RED if c.active else (MUTED if c.count == 0 else FG))

    update_alarm_bar()

    # ---- 电芯卡片 -------------------------------------------------------- #
    scroll_wrap = tk.Frame(root, bg=BG)
    scroll_wrap.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 6))
    scroll_wrap.grid_rowconfigure(0, weight=1)
    scroll_wrap.grid_columnconfigure(0, weight=1)
    canvas = tk.Canvas(scroll_wrap, bg=BG, highlightthickness=0, bd=0)
    canvas.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(scroll_wrap, orient="vertical", command=canvas.yview)
    vsb.grid(row=0, column=1, sticky="ns")
    canvas.configure(yscrollcommand=vsb.set)
    body = tk.Frame(canvas, bg=BG)
    body.grid_columnconfigure(0, weight=1)
    body_win = canvas.create_window((0, 0), window=body, anchor="nw")

    scroll_state = {"w": 0, "h": 0}

    def sync_scroll(_event=None) -> None:
        cw = max(1, canvas.winfo_width())
        ch = max(1, canvas.winfo_height())
        target_w = cw - 2
        target_h = max(body.winfo_reqheight(), ch)
        if (scroll_state["w"], scroll_state["h"]) != (target_w, target_h):
            scroll_state["w"], scroll_state["h"] = target_w, target_h
            canvas.itemconfigure(body_win, width=target_w, height=target_h)
        canvas.configure(scrollregion=(0, 0, target_w, target_h))
        if body.winfo_reqheight() > ch + 4:
            vsb.grid()
        else:
            vsb.grid_remove()
            canvas.yview_moveto(0)

    def on_wheel(event) -> None:
        if body.winfo_reqheight() > canvas.winfo_height() + 4:
            canvas.yview_scroll(-int(event.delta / 120), "units")

    body.bind("<Configure>", sync_scroll)
    canvas.bind("<Configure>", sync_scroll)
    canvas.bind("<MouseWheel>", on_wheel)
    body.bind("<MouseWheel>", on_wheel)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(2, weight=1)

    # ---- 电池总览（SOC / 总压 / 电流） ----------------------------------- #
    batt_bar = tk.Frame(body, bg=BG)
    batt_bar.pack(fill="x")
    for c in range(3):
        batt_bar.grid_columnconfigure(c, weight=1)

    def batt_card(col, title, unit, color=FG, title_size=10, value_size=15):
        f = tk.Frame(batt_bar, bg=CARD2, highlightthickness=1, highlightbackground="#e5e7eb")
        f.grid(row=0, column=col, sticky="nsew", padx=4)
        tk.Label(f, text=title, bg=CARD2, fg=LABEL_FG,
                 font=(FONT_UI, title_size)).pack(anchor="w", padx=12, pady=(3, 0))
        row = tk.Frame(f, bg=CARD2)
        row.pack(anchor="w", padx=12, pady=(0, 3))
        v = tk.Label(row, text="--", bg=CARD2, fg=color,
                     font=(FONT_NUM, value_size, "bold"))
        v.pack(side="left")
        tk.Label(row, text=unit, bg=CARD2, fg=MUTED,
                 font=(FONT_UI, 12)).pack(side="left", padx=(5, 0), pady=(8, 0))
        return v, row

    soc_frame = tk.Frame(batt_bar, bg=CARD2, highlightthickness=1, highlightbackground="#e5e7eb")
    soc_frame.grid(row=0, column=0, sticky="nsew", padx=4)
    tk.Label(soc_frame, text="SOC", bg=CARD2, fg=LABEL_FG,
             font=(FONT_UI, 16)).pack(anchor="w", padx=12, pady=(3, 0))
    soc_canvas = tk.Canvas(soc_frame, width=150, height=150, bg=CARD2, highlightthickness=0)
    soc_canvas.pack(anchor="center", padx=12, pady=(0, 2))
    soc_state: Dict[str, Any] = {"v": None, "photo": None, "key": None, "item": None}

    def soc_color(v: Optional[float]) -> str:
        if v is None:
            return MUTED
        if v < 10:
            return RED
        if v < 30:
            return YELLOW
        if v >= 80:
            return GREEN
        return DARK_BLUE

    def draw_soc(*_args) -> None:
        c = soc_canvas
        size = max(80, int(min(c.winfo_width(), c.winfo_height())))
        v = soc_state["v"]
        color = soc_color(v)
        key = (size, None if v is None else round(v, 1), color)
        if key != soc_state["key"] or soc_state["photo"] is None:
            rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
            b64 = soc_ring_png(v, size, max(10, int(size * 0.085)), rgb)
            photo = tk.PhotoImage(data=b64, master=root)
            soc_state["photo"] = photo
            soc_state["key"] = key
            item = soc_state["item"]
            if item is None:
                soc_state["item"] = c.create_image(size / 2, size / 2, image=photo)
            else:
                c.itemconfigure(item, image=photo)
                c.coords(item, size / 2, size / 2)
        c.delete("label")
        fs = max(11, int(size * 0.19))
        c.create_text(size / 2, size / 2, text=f"{v:.0f}%" if v is not None else "--",
                      fill=color, font=(FONT_NUM, fs, "bold"), tags="label")

    soc_canvas.bind("<Configure>", draw_soc)
    draw_soc()

    volt_lbl, _ = batt_card(1, "总压", "V", title_size=16, value_size=30)
    curr_lbl, curr_row = batt_card(2, "电流", "A", title_size=16, value_size=30)
    curr_state = tk.Label(curr_row, text="--", bg=CARD2, fg=BLUE,
                          font=(FONT_UI, 14))
    curr_state.pack(side="left", padx=(10, 0), pady=(3, 0))

    cell_area = tk.Frame(body, bg=BG)
    cell_area.pack(fill="x", pady=(8, 0))
    for c in range(6):
        cell_area.grid_columnconfigure(c, weight=1)

    cell_vals: List[tk.Label] = []
    for i in range(6):
        f = tk.Frame(cell_area, bg=CARD, highlightthickness=1, highlightbackground="#e5e7eb")
        f.grid(row=0, column=i, sticky="nsew", padx=4)
        title_row = tk.Frame(f, bg=CARD)
        title_row.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(title_row, text=f"电芯 {i + 1}", bg=CARD, fg=LABEL_FG,
                 font=(FONT_UI, 10)).pack(side="left")
        tk.Label(title_row, text="V", bg=CARD, fg=MUTED,
                 font=(FONT_UI, 9)).pack(side="right")
        row = tk.Frame(f, bg=CARD)
        row.pack(anchor="w", padx=12, pady=(2, 8))
        v = tk.Label(row, text="---", bg=CARD, fg=FG, font=(FONT_NUM, 17, "bold"))
        v.pack(side="left")
        cell_vals.append(v)

    # ---- 汇总行 ---------------------------------------------------------- #
    summary = tk.Frame(body, bg=BG)
    summary.pack(fill="x", pady=(6, 0))
    for c in range(4):
        summary.grid_columnconfigure(c, weight=1)

    def big_card(parent, col, title, unit, bold_color=FG):
        f = tk.Frame(parent, bg=CARD2, highlightthickness=1, highlightbackground="#e5e7eb")
        f.grid(row=0, column=col, sticky="nsew", padx=4)
        tk.Label(f, text=title, bg=CARD2, fg=LABEL_FG,
                 font=(FONT_UI, 10)).pack(anchor="w", padx=12, pady=(2, 0))
        v = tk.Label(f, text="--", bg=CARD2, fg=bold_color, font=(FONT_NUM, 15, "bold"))
        v.pack(anchor="w", padx=12)
        u = tk.Label(f, text=unit, bg=CARD2, fg=MUTED, font=(FONT_UI, 8))
        u.pack(anchor="w", padx=12, pady=(0, 2))
        return v

    vmax_lbl = big_card(summary, 0, "最高单体电压", "V")
    vmin_lbl = big_card(summary, 1, "最低单体电压", "V")
    delta_lbl = big_card(summary, 2, "最大压差", "mV")
    cycle_lbl = big_card(summary, 3, "循环次数", "次")

    # ---- 下半部分：温度/MOS/总压 ----------------------------------------- #
    lower = tk.Frame(body, bg=BG)
    lower.pack(fill="both", expand=True, pady=(12, 0))

    left_col = tk.Frame(lower, bg=BG)
    left_col.pack(side="left", fill="both", expand=True)
    left_col.grid_rowconfigure(0, weight=1)
    left_col.grid_columnconfigure(0, weight=1)

    temp_frame = tk.Frame(left_col, bg=CARD, highlightthickness=1, highlightbackground="#e5e7eb")
    temp_frame.grid(row=0, column=0, sticky="nsew", padx=4)
    tk.Label(temp_frame, text="温度", bg=CARD, fg=LABEL_FG,
             font=(FONT_UI, 10)).pack(anchor="w", padx=12, pady=(8, 2))
    temp_vals: Dict[str, tk.Label] = {}
    for key, label in (("mos", "THM0  MOS"), ("socket", "THM1  插座"), ("cell", "THM2  电芯")):
        row = tk.Frame(temp_frame, bg=CARD)
        row.pack(fill="x", padx=12, pady=0)
        tk.Label(row, text=label, bg=CARD, fg=FG, font=(FONT_UI, 10)).pack(side="left")
        tk.Label(row, text="℃", bg=CARD, fg=MUTED,
                 font=(FONT_UI, 9)).pack(side="right", padx=(3, 0))
        val = tk.Label(row, text="--", bg=CARD, fg=FG, font=(FONT_NUM, 14, "bold"))
        val.pack(side="right")
        temp_vals[key] = val

    tk.Frame(temp_frame, bg="#e5e7eb", height=1).pack(fill="x", padx=12, pady=(1, 0))
    drow = tk.Frame(temp_frame, bg=CARD)
    drow.pack(fill="x", padx=12, pady=0)
    tk.Label(drow, text="温差  THM0~2", bg=CARD, fg=LABEL_FG,
             font=(FONT_UI, 9)).pack(side="left")
    tk.Label(drow, text="℃", bg=CARD, fg=MUTED,
             font=(FONT_UI, 9)).pack(side="right", padx=(3, 0))
    dval = tk.Label(drow, text="--", bg=CARD, fg=BLUE, font=(FONT_NUM, 14, "bold"))
    dval.pack(side="right")
    temp_vals["delta"] = dval

    # ---- 均衡状态（温度框下方） ------------------------------------------ #
    bal_frame = tk.Frame(left_col, bg=CARD, highlightthickness=1, highlightbackground="#e5e7eb")
    bal_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(8, 0))
    bal_row = tk.Frame(bal_frame, bg=CARD)
    bal_row.pack(fill="x", padx=12, pady=(6, 6))
    tk.Label(bal_row, text="均衡状态", bg=CARD, fg=LABEL_FG,
             font=(FONT_UI, 10)).pack(side="left")
    bal_dots: List[tk.Label] = []
    for idx in range(6):
        tk.Label(bal_row, text=f"C{idx + 1:02d}", bg=CARD, fg=LABEL_FG,
                 font=(FONT_UI, 9)).pack(side="left", padx=(8, 0))
        dot = tk.Label(bal_row, text="●", bg=CARD, fg="#d1d5db", font=(FONT_UI, 11))
        dot.pack(side="left", padx=(1, 0))
        bal_dots.append(dot)
    bal_sum = tk.Label(bal_row, text="未记录", bg=CARD, fg=MUTED, font=(FONT_UI, 9))
    bal_sum.pack(side="right")

    mos_frame = tk.Frame(lower, bg=CARD, highlightthickness=1, highlightbackground="#e5e7eb")
    mos_frame.pack(side="left", fill="both", expand=True, padx=4)
    tk.Label(mos_frame, text="MOS 状态", bg=CARD, fg=LABEL_FG,
             font=(FONT_UI, 10)).pack(anchor="w", padx=12, pady=(8, 2))
    mos_vals: Dict[str, tk.Label] = {}
    for key, label in (("charge", "充电 MOS"), ("discharge", "放电 MOS")):
        row = tk.Frame(mos_frame, bg=CARD)
        row.pack(fill="x", padx=12, pady=1)
        tk.Label(row, text=label, bg=CARD, fg=FG, font=(FONT_UI, 10)).pack(side="left")
        val = tk.Label(row, text="--", bg=CARD, fg=MUTED, font=(FONT_UI, 12, "bold"))
        val.pack(side="right")
        mos_vals[key] = val

    # ---- 在线更新 -------------------------------------------------------- #
    def check_update() -> None:
        url = str((cfg.get("update") or {}).get("manifest_url") or "").strip()
        if not url:
            messagebox.showinfo(
                "检查更新",
                "还没有配置更新地址。\n请在 config.json 的 update.manifest_url 填写更新清单地址。")
            return
        if update_state["busy"]:
            return
        update_state["busy"] = True
        hint.config(text="正在检查更新…", fg=MUTED)

        def work() -> None:
            try:
                m = updater.fetch_manifest(
                    url, float((cfg.get("update") or {}).get("timeout_s", 15)),
                    str((cfg.get("update") or {}).get("proxy") or ""))
                upd_q.put(("manifest", m))
            except Exception as exc:
                upd_q.put(("error", updater.friendly_error(exc, url)))

        threading.Thread(target=work, daemon=True).start()

    def start_download(m: Dict[str, Any]) -> None:
        version = str(m.get("version") or "")
        url = str(m.get("exe_url") or m.get("url") or "")
        sha = str(m.get("exe_sha256") or m.get("sha256") or "")
        if not url:
            update_state["busy"] = False
            messagebox.showerror("更新", "更新清单里没有下载地址（exe_url）")
            return
        hint.config(text=f"正在下载 {version}…", fg=MUTED)

        def work() -> None:
            try:
                dest = Path(tempfile.gettempdir()) / "BmsHostUpdate" / f"BmsAgingHost_{version}.exe"
                last = [-1]

                def prog(done: int, total: int) -> None:
                    pct = int(done * 100 / total) if total else -1
                    if pct != last[0]:
                        last[0] = pct
                        upd_q.put(("progress", (done, total)))

                updater.download_file(
                    url, dest, float((cfg.get("update") or {}).get("timeout_s", 15)), prog,
                    str((cfg.get("update") or {}).get("proxy") or ""))
                if sha and updater.sha256_file(dest).lower() != sha.lower():
                    raise ValueError("SHA256 校验不通过")
                upd_q.put(("downloaded", (dest, version)))
            except Exception as exc:
                upd_q.put(("error", updater.friendly_error(exc, url)))

        threading.Thread(target=work, daemon=True).start()

    def apply_update(dest: Path, version: str) -> None:
        target = updater.launcher_path()
        if target is None:
            update_state["busy"] = False
            messagebox.showinfo(
                "更新已下载",
                f"新版已下载到：\n{dest}\n\n当前是文件夹版，请用新版替换文件夹后再运行。")
            return
        try:
            script = updater.make_update_script(dest, target, os.getpid())
            updater.launch_update_script(script)
        except Exception as exc:
            update_state["busy"] = False
            messagebox.showerror("更新失败", str(exc))
            return
        messagebox.showinfo("更新", f"{version} 已下载，程序将退出并自动重启完成更新。")
        root.after(800, quit_app)

    def process_updates() -> None:
        try:
            while True:
                kind, payload = upd_q.get_nowait()
                if kind == "manifest":
                    m = payload
                    remote = str(m.get("version") or "")
                    if not remote or not updater.is_newer(remote, APP_VERSION):
                        update_state["busy"] = False
                        hint.config(text="")
                        messagebox.showinfo("检查更新", f"当前已是最新版本：{APP_VERSION}")
                    else:
                        notes = str(m.get("notes") or "").strip()
                        msg = f"发现新版本\n\n当前：{APP_VERSION}\n最新：{remote}"
                        if notes:
                            msg += f"\n\n更新内容：\n{notes}"
                        msg += "\n\n现在下载并更新吗？"
                        if messagebox.askyesno("检查更新", msg):
                            start_download(m)
                        else:
                            update_state["busy"] = False
                elif kind == "progress":
                    done, total = payload
                    pct = f"{int(done * 100 / total)}%" if total else f"{done // 1024} KB"
                    hint.config(text=f"正在下载更新… {pct}", fg=MUTED)
                elif kind == "downloaded":
                    dest, version = payload
                    apply_update(dest, version)
                elif kind == "error":
                    update_state["busy"] = False
                    hint.config(text="")
                    messagebox.showerror("检查更新", str(payload))
        except queue.Empty:
            pass
        root.after(200, process_updates)

    # ---- 底部 ------------------------------------------------------------ #
    bottom = tk.Frame(root, bg=BG)
    bottom.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))

    info = tk.Label(bottom, text="", bg=BG, fg=MUTED, anchor="w",
                    font=(FONT_UI, 9))
    info.pack(fill="x")
    save_lbl = tk.Label(bottom, text="", bg=BG, fg=MUTED, anchor="w",
                        font=(FONT_UI, 8))
    save_lbl.pack(fill="x", pady=(0, 4))

    btns = tk.Frame(bottom, bg=BG)
    btns.pack(fill="x")

    def open_dir() -> None:
        try:
            os.startfile(str(out_dir))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    ttk.Button(btns, text="打开记录", command=open_dir).pack(side="right", padx=4)
    ttk.Button(btns, text="检查更新", command=check_update).pack(side="right", padx=4)

    def quit_app() -> None:
        stop_flag.set()
        if reader is not None:
            reader.stop()
        detail_csv.close()
        alarm_csv.close()
        try:
            root.destroy()
        except Exception:
            pass

    ttk.Button(btns, text="退出", command=quit_app).pack(side="right", padx=4)

    hint = tk.Label(btns, text="", bg=BG, fg=MUTED, font=(FONT_UI, 8))
    hint.pack(side="left")

    # ---- 定时刷新 -------------------------------------------------------- #
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def refresh() -> None:
        nonlocal last_alarm_eval, record_count, last_record_updated
        snap = reader.snapshot() if reader else None
        if snap:
            d: BmsData = snap["data"]
            st = snap["status"]
            if not snap["connected"] and st in ("连接失败", "已断开"):
                connect_btn.config(text="连接")
            set_status(st, snap["detail"])
            color = {"已连接": GREEN, "连接中": YELLOW, "未连接": YELLOW,
                     "连接失败": RED, "已断开": RED}.get(st, YELLOW)
            status_dot.config(fg=color)

            vmax_mv = max(d.cells) if d.cells else None
            vmin_mv = min(d.cells) if d.cells else None
            imax = d.cells.index(vmax_mv) if d.cells else -1
            imin = d.cells.index(vmin_mv) if d.cells else -1
            for i in range(6):
                if i < len(d.cells):
                    mv = d.cells[i]
                    color = GREEN
                    if vmax_mv != vmin_mv:
                        if i == imax:
                            color = RED
                        elif i == imin:
                            color = BLUE
                    cell_vals[i].config(text=f"{mv / 1000:.3f}", fg=color)
                else:
                    cell_vals[i].config(text="---", fg=MUTED)

            if d.cells:
                vmax, vmin = max(d.cells), min(d.cells)
                delta = vmax - vmin
                vmax_lbl.config(text=f"{vmax / 1000:.3f}",
                                fg=RED if vmax > float(ath.get("cell_max_v", 4.3)) * 1000 else FG)
                vmin_lbl.config(text=f"{vmin / 1000:.3f}",
                                fg=RED if vmin < float(ath.get("cell_min_v", 2.5)) * 1000 else FG)
                delta_lbl.config(text=f"{delta}", fg=RED if delta > float(ath.get("delta_mv", 200)) else FG)
            else:
                vmax_lbl.config(text="--", fg=FG)
                vmin_lbl.config(text="--", fg=FG)
                delta_lbl.config(text="--", fg=FG)

            cycle_lbl.config(text=str(d.cycle_count) if d.cycle_count is not None else "--")
            soc_state["v"] = d.soc
            draw_soc()

            for key, val in (("mos", d.mos_temp), ("socket", d.socket_temp), ("cell", d.cell_temp)):
                temp_vals[key].config(
                    text=f"{val:.1f}" if val is not None else "--",
                    fg=RED if (val is not None and val > float(ath.get("temp_warn_c", 65))) else FG)
            temp_vals["delta"].config(
                text=f"{d.temp_delta:.1f}" if d.temp_delta is not None else "--",
                fg=RED if (d.temp_delta is not None and
                           d.temp_delta > float(ath.get("temp_delta_warn_c", 20))) else BLUE)

            def mos_style(v: Optional[bool]) -> tuple:
                if v is None:
                    return "--", MUTED
                return ("ON", GREEN) if v else ("OFF", MUTED)
            t, c = mos_style(d.charge_mos)
            mos_vals["charge"].config(text=t, fg=c)
            t, c = mos_style(d.discharge_mos)
            mos_vals["discharge"].config(text=t, fg=c)
            bal = d.balance
            if bal:
                on = [i + 1 for i, b in enumerate(bal[:6]) if b]
                for i, dot in enumerate(bal_dots):
                    dot.config(fg=GREEN if (i < len(bal) and bal[i]) else "#d1d5db")
                bal_sum.config(
                    text=("均衡中：" + " ".join(f"C{x:02d}" for x in on)) if on else "未均衡",
                    fg=GREEN if on else MUTED)
            else:
                for dot in bal_dots:
                    dot.config(fg="#d1d5db")
                bal_sum.config(text="未记录", fg=MUTED)
            ct = charge_text(d)
            curr_state.config(text=ct + ("  ⚠" if d.alarm else ""),
                              fg=RED if (ct == "放电" or d.alarm) else (BLUE if ct == "充电" else MUTED))

            volt_lbl.config(text=f"{d.total_v:.2f}" if d.total_v is not None else "--",
                            fg=FG if d.total_v is not None else MUTED)
            curr_lbl.config(text=f"{d.current_a:.1f}" if d.current_a is not None else "--",
                            fg=FG if d.current_a is not None else MUTED)

            # 告警判断：只在收到新样本时评估，上升沿计一条
            if d.updated is not None and d.updated != last_alarm_eval:
                last_alarm_eval = d.updated
                for ev in alarm_book.evaluate(d, d.updated):
                    if ev["kind"] == "触发":
                        beep()
                        hint.config(text=f"{fmt_hms(ev['cond'].since)} {ev['cond'].name}：{ev['detail']}",
                                    fg=RED)
            update_alarm_bar()

            age = time.time() - snap["last_rx"] if snap["last_rx"] else None
            info.config(
                text=(f"最后更新 {fmt_hms(d.updated) if d.updated else '--'}　·　"
                      f"帧 {snap['frames']}　超时 {snap['timeouts']}　·　"
                      + (f"数据 {age:.1f}s 前" if age is not None else "等待数据")),
                fg=MUTED if st == "已连接" else YELLOW)
        else:
            set_status("未连接", "")
            status_dot.config(fg=YELLOW)
            info.config(text="选择串口后点击“连接”", fg=MUTED)

        # 每一帧数据写入当前 30 分钟文档
        if snap and snap["data"].updated is not None and snap["data"].updated != last_record_updated:
            last_record_updated = snap["data"].updated
            try:
                detail_csv.write(snap["data"].updated,
                                 detail_row(snap["data"], alarm_book.active_text()))
                record_count += 1
            except Exception as exc:
                hint.config(text=f"记录失败: {exc}", fg=RED)

        if detail_csv.path:
            save_lbl.config(
                text=(f"每 {window_min} 分钟一个文件　当前 {detail_csv.path.name}"
                      f"　本段 {detail_csv.rows} 条　累计 {record_count} 条"))
        else:
            save_lbl.config(text=f"记录目录 {out_dir}（等待第一帧数据）")
        root.after(200, refresh)

    # ---- 字体随窗口大小自适应 -------------------------------------------- #
    base_fonts: Dict[Any, tuple] = {}
    ui_state = {"scale": 1.0, "job": None}

    def walk_widgets(w):
        yield w
        for child in w.winfo_children():
            yield from walk_widgets(child)

    def snapshot_fonts() -> None:
        for w in walk_widgets(root):
            if isinstance(w, ttk.Widget):
                continue
            try:
                spec = w.cget("font")
            except Exception:
                continue
            if not spec:
                continue
            try:
                f = tkfont.Font(root=root, font=spec)
                fam = f.actual("family")
                size = int(f.actual("size"))
                weight = f.actual("weight")
            except Exception:
                continue
            if size > 0:
                base_fonts[w] = (fam, size, weight)

    def apply_scale(scale: float) -> None:
        for w, (fam, size, weight) in list(base_fonts.items()):
            try:
                w.configure(font=(fam, max(7, round(size * scale)), weight))
            except Exception:
                pass
        try:
            style.configure("TButton", font=(FONT_UI, max(8, round(10 * scale))))
            style.configure("TCombobox", font=(FONT_UI, max(8, round(10 * scale))))
        except Exception:
            pass
        try:
            soc_canvas.config(width=int(150 * scale), height=int(150 * scale))
            draw_soc()
        except Exception:
            pass

    def on_root_configure(event) -> None:
        if event.widget is not root:
            return
        w = max(200, root.winfo_width())
        h = max(200, root.winfo_height())
        scale = max(0.70, min(1.6, min(w / 1180.0, h / 960.0)))
        if abs(scale - ui_state["scale"]) < 0.05:
            return
        ui_state["scale"] = scale
        if ui_state["job"]:
            try:
                root.after_cancel(ui_state["job"])
            except Exception:
                pass
        ui_state["job"] = root.after(200, lambda s=scale: apply_scale(s))

    snapshot_fonts()
    root.bind("<Configure>", on_root_configure)

    refresh_ports()
    if not port_var.get():
        ports_now = list_ports()
        if ports_now:
            port_var.set(ports_now[0])

    if demo:
        root.after(300, start_reader)

    root.after(200, refresh)
    root.after(200, process_updates)
    root.mainloop()
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def single_instance() -> bool:
    """同一时间只允许一个界面实例；第二个实例直接退出。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Local\\BmsAgingHostV1")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="电池老化测试上位机")
    parser.add_argument("--demo", action="store_true", help="模拟数据")
    parser.add_argument("--window-min", type=int, default=0, help="每段记录文件的分钟数，默认 30")
    parser.add_argument("--port", type=str, default="")
    parser.add_argument("--baud", type=int, default=0)
    parser.add_argument("--check-update", type=str, default="",
                        help="只检查更新清单并打印结果（不下载）")
    args = parser.parse_args(argv)
    cfg = load_config()

    if args.check_update:
        code = 0
        try:
            proxy = str((cfg.get("update") or {}).get("proxy") or "")
            m = updater.fetch_manifest(args.check_update, 15, proxy)
            remote = str(m.get("version") or "")
            print(json.dumps(m, ensure_ascii=False, indent=2))
            print("remote=%s local=%s newer=%s" %
                  (remote, APP_VERSION, updater.is_newer(remote, APP_VERSION)))
        except Exception as exc:
            print(updater.friendly_error(exc, args.check_update))
            code = 1
        try:
            if sys.stdin and sys.stdin.isatty():
                input("\n按回车退出…")
        except Exception:
            pass
        return code

    if args.port:
        cfg["port"] = args.port
    if args.baud:
        cfg["baud"] = args.baud
    if args.window_min:
        cfg["record_window_min"] = args.window_min

    if not args.demo and not single_instance():
        return 0

    return run_gui(cfg, demo=args.demo)


if __name__ == "__main__":
    sys.exit(main())
