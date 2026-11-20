# -*- coding: utf-8 -*-
"""BMS 0x7B/0x7D 协议：帧封装、CRC8、应答解码。

由 BmsStudioV3Qt 3.8.13 Qt030 的行为还原并逐字节验证：
    帧: 7B | LEN | DATA(LEN 字节) | CRC8 | 7D
    LEN = CMD + payload 的字节数
    CRC8: poly=0x2A, init=0x00, refin=false, xorout=0x00，覆盖 LEN+DATA

应答 DATA[0] = 命令字，DATA[1:] = 负载。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

SOF = 0x7B
EOF = 0x7D
MAX_LEN = 124
MAX_FRAME = 128

CMD_NAMES = {
    0x20: "版本",
    0x30: "单体电压",
    0x31: "电芯温度",
    0x32: "MOS/插座温度",
    0x33: "系统信息",
    0x50: "实时状态",
}


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x2A) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_request(cmd: int) -> bytes:
    body = bytes([cmd])
    length = len(body)
    frame = bytes([SOF, length]) + body
    frame += bytes([crc8(frame[1:]), EOF])
    return frame


class FrameParser:
    """增量喂字节，输出 (cmd, payload, raw)。"""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.crc_errors = 0
        self.length_errors = 0

    def reset(self) -> None:
        self.buf.clear()

    def feed(self, data: bytes) -> List[Tuple[int, bytes, bytes]]:
        self.buf.extend(data)
        out: List[Tuple[int, bytes, bytes]] = []
        while True:
            start = self.buf.find(SOF)
            if start < 0:
                self.buf.clear()
                break
            if start > 0:
                del self.buf[:start]
            if len(self.buf) < 3:
                break
            length = self.buf[1]
            if length < 1 or length > MAX_LEN:
                self.length_errors += 1
                del self.buf[0]
                continue
            total = 2 + length + 2  # SOF LEN DATA CRC EOF
            if len(self.buf) < total:
                break
            frame = bytes(self.buf[:total])
            if frame[-1] != EOF:
                self.length_errors += 1
                del self.buf[0]
                continue
            if crc8(frame[1:2 + length]) != frame[2 + length]:
                self.crc_errors += 1
                del self.buf[0]
                continue
            out.append((frame[2], frame[3:2 + length], frame))
            del self.buf[:total]
        if len(self.buf) > MAX_FRAME * 4:
            self.buf.clear()
        return out


# --------------------------------------------------------------------------- #
# 基础类型
# --------------------------------------------------------------------------- #

def _u16(data: bytes, off: int) -> int:
    return (data[off] << 8) | data[off + 1]


def _i16(data: bytes, off: int) -> int:
    v = _u16(data, off)
    return v - 0x10000 if v & 0x8000 else v


def _u32(data: bytes, off: int) -> int:
    return (data[off] << 24) | (data[off + 1] << 16) | (data[off + 2] << 8) | data[off + 3]


def _i32(data: bytes, off: int) -> int:
    v = _u32(data, off)
    return v - 0x100000000 if v & 0x80000000 else v


def _has(data: bytes, off: int, n: int = 2) -> bool:
    return len(data) >= off + n


def _bits(byte: int, n: int) -> bool:
    return bool(byte & (1 << n))


# --------------------------------------------------------------------------- #
# 解码
# --------------------------------------------------------------------------- #

def decode_version(p: bytes) -> Dict:
    if len(p) < 9:
        return {"software_version": None, "hardware_version": None, "protocol_version": None}
    return {
        "software_version": [p[0], p[1], p[2]],
        "hardware_version": [p[3], p[4], p[5]],
        "protocol_version": [p[6], p[7], p[8]],
    }


def decode_cells(p: bytes) -> Dict:
    cells: List[int] = []
    for i in range(0, min(len(p), 28) - 1, 2):
        cells.append(_u16(p, i))
    return {"cells_mV": cells}


def decode_cell_temp(p: bytes) -> Dict:
    # 0x31: 电芯温度(THM2)。固件把同一个值写入两个槽位，取第 1 个。
    values = []
    for i in range(0, min(len(p), 8) - 1, 2):
        values.append(_i16(p, i))
    if not values:
        return {"cell_temp_C": None}
    return {"cell_temp_C": float(values[0]), "cell_temps_C": values}


def decode_other_temp(p: bytes) -> Dict:
    # 0x32: 0-1 MOS 温度, 16-17 正极(插座)温度, 18-19 负极温度, 28-29 PCB 温度
    def temp(off: int) -> Optional[float]:
        return float(_i16(p, off)) if _has(p, off) else None
    return {
        "mos_temp_C": temp(0),
        "socket_temp_C": temp(16),
        "negative_connector_temp_C": temp(18),
        "pcb_temp_C": temp(28),
    }


def decode_sys_info(p: bytes) -> Dict:
    out: Dict = {
        "design_capacity_mAh": None,
        "cell_count": None,
        "temperature_count": None,
        "rated_voltage_V": None,
        "cell_vmax_mV": None,
        "cell_vmin_mV": None,
        "cycle_count": None,
        "battery_datetime": None,
    }
    if _has(p, 0):
        out["design_capacity_mAh"] = _u16(p, 0) * 100
    if _has(p, 2):
        out["cell_count"] = _u16(p, 2)
    if _has(p, 4):
        out["temperature_count"] = _u16(p, 4)
    if _has(p, 6):
        out["rated_voltage_V"] = _u16(p, 6) / 10.0
    if _has(p, 8):
        out["cell_vmax_mV"] = _u16(p, 8)
    if _has(p, 10):
        out["cell_vmin_mV"] = _u16(p, 10)
    if _has(p, 50):
        out["cycle_count"] = _u16(p, 50)
    if _has(p, 52, 6):
        parts = [p[52], p[53], p[54], p[55], p[56], p[57]]
        out["battery_datetime"] = [2000 + parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]]
    return out


def decode_status(p: bytes) -> Dict:
    """0x50 实时状态。"""
    out: Dict = {
        "pack_voltage_V": None,
        "current_A": None,
        "soc_pct": None,
        "soh_pct": None,
        "cell_vmax_mV": None,
        "cell_vmax_index": None,
        "cell_vmin_mV": None,
        "cell_vmin_index": None,
        "cell_temp_max_C": None,
        "cell_temp_min_C": None,
        "remaining_capacity_mAh": None,
        "total_voltage_V": None,
        "total_current_A": None,
        "charging": False,
        "discharging": False,
        "charge_mos": False,
        "discharge_mos": False,
        "precharge_mos": False,
        "charger_detected": False,
        "load_detected": False,
        "balance": [],
        "alarm_raw": "",
        "alarm_active": False,
    }
    if _has(p, 0):
        out["pack_voltage_V"] = _u16(p, 0) / 10.0
    if _has(p, 2):
        out["current_A"] = _i16(p, 2) / 10.0
    if _has(p, 4):
        out["soc_pct"] = _u16(p, 4) / 10.0
    if _has(p, 6):
        out["soh_pct"] = _u16(p, 6) / 10.0
    if _has(p, 8):
        out["cell_vmax_mV"] = _u16(p, 8)
    if _has(p, 10):
        out["cell_vmax_index"] = _u16(p, 10)
    if _has(p, 12):
        out["cell_vmin_mV"] = _u16(p, 12)
    if _has(p, 14):
        out["cell_vmin_index"] = _u16(p, 14)
    if _has(p, 16):
        out["cell_temp_max_C"] = _i16(p, 16) / 10.0
    if _has(p, 20):
        out["cell_temp_min_C"] = _i16(p, 20) / 10.0
    if _has(p, 28):
        out["remaining_capacity_mAh"] = _u16(p, 28)
    if _has(p, 30, 4):
        out["total_voltage_V"] = _u32(p, 30) / 1000.0
    if _has(p, 34, 4):
        out["total_current_A"] = _i32(p, 34) / 1000.0
    if _has(p, 38, 1):
        out["charging"] = _bits(p[38], 0)
        out["discharging"] = _bits(p[38], 1)
    if _has(p, 39, 1):
        out["charge_mos"] = _bits(p[39], 0)
        out["discharge_mos"] = _bits(p[39], 1)
        out["precharge_mos"] = _bits(p[39], 2)
    if _has(p, 40, 1):
        out["charger_detected"] = _bits(p[40], 0)
        out["load_detected"] = _bits(p[40], 1)
    if _has(p, 41, 1):
        # 0x50 Data[41] bit0~bit5 -> C01~C06，1 均衡开启，0 关闭
        out["balance"] = [bool(p[41] & (1 << i)) for i in range(6)]
    if _has(p, 49, 14):
        alarm = p[49:63]
        out["alarm_raw"] = alarm.hex(" ").upper()
        out["alarm_active"] = any(alarm)
    return out


DECODERS = {
    0x20: decode_version,
    0x30: decode_cells,
    0x31: decode_cell_temp,
    0x32: decode_other_temp,
    0x33: decode_sys_info,
    0x50: decode_status,
}


def decode_payload(cmd: int, payload: bytes) -> Optional[Dict]:
    fn = DECODERS.get(cmd)
    if fn is None:
        return None
    return fn(payload)


def parse_frames(data: bytes) -> List[Tuple[int, bytes, bytes]]:
    parser = FrameParser()
    return parser.feed(data)


if __name__ == "__main__":
    # 自检
    assert build_request(0x20).hex() == "7b01202e7d", build_request(0x20).hex()
    assert build_request(0x50).hex() == "7b0150b67d", build_request(0x50).hex()
    frames = parse_frames(bytes.fromhex("7B 0A 20 01 00 01 01 00 02 01 00 01 26 7D"))
    assert len(frames) == 1 and frames[0][0] == 0x20
    v = decode_payload(0x20, frames[0][1])
    assert v["software_version"] == [1, 0, 1] and v["hardware_version"] == [1, 0, 2]
    cells = decode_payload(0x30, bytes.fromhex("0F 3A 0F 00"))
    assert cells["cells_mV"] == [3898, 3840]
    t = decode_payload(0x31, bytes.fromhex("00 23 00 23"))
    assert t["cell_temp_C"] == 35.0
    o = decode_payload(0x32, bytes(32))
    assert o["mos_temp_C"] == 0.0
    s = decode_payload(0x50, bytes(63))
    assert s["pack_voltage_V"] == 0.0
    print("bms_protocol self-test OK")
