# -*- coding: utf-8 -*-
"""离线自检：协议帧 + 读取流程（用假串口）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bms_protocol as proto
import app as A


def frame(cmd: int, payload: bytes) -> bytes:
    body = bytes([cmd]) + payload
    head = bytes([proto.SOF, len(body)]) + body
    return head + bytes([proto.crc8(head[1:]), proto.EOF])


class FakeSerial:
    def __init__(self):
        self.pending = bytearray()
        self.written = []
        self.opened = False

    def open(self, port, baud):
        self.opened = True

    def close(self):
        self.opened = False

    def write(self, data):
        self.written.append(bytes(data))
        cmd = data[2]
        self.pending.extend(self._response(cmd))
        return len(data)

    def read(self, max_bytes=512, timeout_ms=30):
        if not self.pending:
            return b""
        n = min(max_bytes, len(self.pending))
        out = bytes(self.pending[:n])
        del self.pending[:n]
        return out

    def _response(self, cmd):
        if cmd == 0x20:
            return frame(cmd, bytes.fromhex("01 00 01 01 00 02 01 00 01"))
        if cmd == 0x30:
            cells = [3850, 3841, 3862, 3853, 3844, 3819]
            p = b"".join(c.to_bytes(2, "big") for c in cells)
            return frame(cmd, p)
        if cmd == 0x31:
            return frame(cmd, (33).to_bytes(2, "big") * 2)
        if cmd == 0x32:
            p = bytearray(32)
            p[0:2] = (36).to_bytes(2, "big")
            p[16:18] = (41).to_bytes(2, "big")
            p[18:20] = (40).to_bytes(2, "big")
            p[28:30] = (45).to_bytes(2, "big")
            return frame(cmd, bytes(p))
        if cmd == 0x33:
            p = bytearray(58)
            p[2:4] = (6).to_bytes(2, "big")
            p[4:6] = (3).to_bytes(2, "big")
            p[50:52] = (321).to_bytes(2, "big")
            return frame(cmd, bytes(p))
        if cmd == 0x50:
            p = bytearray(63)
            p[2:4] = (-118).to_bytes(2, "big", signed=True)  # -11.8 A
            p[4:6] = (550).to_bytes(2, "big")
            p[6:8] = (990).to_bytes(2, "big")
            p[8:10] = (3862).to_bytes(2, "big")
            p[10:12] = (2).to_bytes(2, "big")
            p[12:14] = (3819).to_bytes(2, "big")
            p[14:16] = (5).to_bytes(2, "big")
            p[16:18] = (330).to_bytes(2, "big")
            p[20:22] = (320).to_bytes(2, "big")
            p[30:34] = (23059).to_bytes(4, "big")   # 23.059 V
            p[34:38] = (-11800).to_bytes(4, "big", signed=True)  # -11.8 A
            p[38] = 0x02   # discharging
            p[39] = 0x03   # both MOS on
            p[40] = 0x02   # load
            p[41] = 0x2A   # C02/C04/C06 均衡
            return frame(cmd, bytes(p))
        return b""


def test_protocol():
    assert proto.build_request(0x20) == bytes.fromhex("7b01202e7d")
    assert proto.build_request(0x50) == bytes.fromhex("7b0150b67d")
    # 分片 + 噪声
    f = frame(0x20, bytes.fromhex("01 00 01 01 00 02 01 00 01"))
    parser = proto.FrameParser()
    out = []
    out += parser.feed(b"\x00\x01" + f[:5])
    out += parser.feed(f[5:])
    assert len(out) == 1 and out[0][0] == 0x20
    print("protocol ok")


def test_reader():
    cfg = A.load_config()
    r = A.BmsReader("COM_TEST", 19200, cfg)
    r.ser = FakeSerial()
    assert r._query(0x20, timeout=0.5) is not None
    assert r._query(0x33, timeout=0.5) is not None
    assert r._query(0x30, timeout=0.5) is not None
    assert r._query(0x31, timeout=0.5) is not None
    assert r._query(0x32, timeout=0.5) is not None
    assert r._query(0x50, timeout=0.5) is not None
    d = r.snapshot()["data"]
    assert d.cells == [3850, 3841, 3862, 3853, 3844, 3819], d.cells
    assert d.cell_count == 6
    assert d.cycle_count == 321
    assert d.balance == [False, True, False, True, False, True], d.balance
    assert d.cell_temp == 33.0
    assert d.mos_temp == 36.0
    assert d.socket_temp == 41.0
    assert abs(d.total_v - 23.059) < 1e-6
    assert abs(d.current_a + 11.8) < 1e-6
    assert d.discharging is True and d.charging is False
    assert d.charge_mos is True and d.discharge_mos is True
    row = A.detail_row(d, "单体过压")
    col = {name: i for i, name in enumerate(A.DETAIL_HEADER)}
    assert row[col["充放电状态"]] == "放电", row
    assert row[col["报警状态"]] == "报警"
    assert row[col["报警类型"]] == "单体过压"
    assert row[col["循环次数"]] == "321"
    assert row[col["温差(℃)"]] == "8.0"
    assert row[col["电芯1(V)"]] == "3.850"
    assert row[col["最高单体(V)"]] == "3.862"
    assert row[col["最低单体(V)"]] == "3.819"
    assert row[col["压差(mV)"]] == "43"
    assert row[col["电流(A)"]] == "-11.8"
    assert row[col["SOC(%)"]] == "55"
    print("reader ok:", row[:6])


def test_csv():
    import tempfile
    from datetime import datetime, timedelta
    folder = Path(tempfile.mkdtemp(prefix="aging_csv_"))
    w = A.WindowCsv(folder, 30)
    cfg = A.load_config()
    r = A.BmsReader("COM_TEST", 19200, cfg)
    r.ser = FakeSerial()
    for cmd in (0x20, 0x33, 0x30, 0x31, 0x32, 0x50):
        r._query(cmd, timeout=0.5)
    d = r.snapshot()["data"]
    t0 = datetime.now().astimezone().replace(hour=10, minute=5, second=0, microsecond=0)
    d.updated = t0
    w.write(d.updated, A.detail_row(d, "正常"))
    w.write(d.updated + timedelta(minutes=10), A.detail_row(d, "正常"))
    d.updated = t0 + timedelta(minutes=26)   # 跨到 10:30 段
    w.write(d.updated, A.detail_row(d, "单体过压"))
    files = sorted(folder.glob("*.csv"))
    assert len(files) == 2, [f.name for f in files]
    text = files[0].read_text(encoding="utf-8-sig").splitlines()
    assert len(text) == 3, text   # 表头 + 2 条
    assert "时间" in text[0] and "充放电状态" in text[0]
    text2 = files[1].read_text(encoding="utf-8-sig").splitlines()
    assert len(text2) == 2 and "单体过压" in text2[1]
    assert "循环次数" in text[0] and "压差(mV)" in text[0] and "报警类型" in text[0]
    print("csv ok:", [f.name for f in files])


def test_header_migration():
    import tempfile
    from datetime import datetime
    folder = Path(tempfile.mkdtemp(prefix="aging_hdr_"))
    old = folder / "老化记录_20261123_1000.csv"
    old.write_text("时间,警报状态,SOC(%)\n2026-11-23 10:01:00,正常,55\n",
                   encoding="utf-8-sig")
    cfg = A.load_config()
    r = A.BmsReader("COM_TEST", 19200, cfg)
    r.ser = FakeSerial()
    for cmd in (0x20, 0x33, 0x30, 0x31, 0x32, 0x50):
        r._query(cmd, timeout=0.5)
    d = r.snapshot()["data"]
    t = datetime.now().astimezone().replace(hour=10, minute=5, second=0, microsecond=0)
    d.updated = t
    w = A.WindowCsv(folder, 30)
    w.write(t, A.detail_row(d, "正常"))
    w.close()
    names = sorted(p.name for p in folder.glob("*.csv"))
    assert "老化记录_20261123_1000_旧格式1.csv" in names, names
    new = folder / "老化记录_20261123_1000.csv"
    lines = new.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 2 and "报警类型" in lines[0], lines
    print("header migration ok:", names)


def test_alarm():
    import tempfile
    from datetime import timedelta
    folder = Path(tempfile.mkdtemp(prefix="aging_alarm_"))
    w = A.DailyCsv(folder, "告警记录", A.ALARM_HEADER)
    book = A.AlarmBook({"alarms": {"cell_max_v": 4.3, "cell_min_v": 2.5,
                                   "delta_mv": 200, "temp_c": 75}}, w)
    t0 = A.local_now()
    d = A.BmsData(cells=[4350, 3800, 2450], mos_temp=78.0, socket_temp=40.0,
                  cell_temp=35.0, updated=t0)
    ev = book.evaluate(d, t0)
    kinds = sorted(e["cond"].key for e in ev if e["kind"] == "触发")
    assert kinds == ["delta", "temp", "vmax", "vmin"], kinds
    assert book.total == 4

    d2 = A.BmsData(cells=[3850, 3840, 3830], mos_temp=40.0, socket_temp=39.0,
                   cell_temp=33.0, updated=t0 + timedelta(seconds=30))
    ev2 = book.evaluate(d2, t0 + timedelta(seconds=30))
    assert len([e for e in ev2 if e["kind"] == "恢复"]) == 4
    assert book.total == 4
    w.close()
    lines = list(folder.glob("*.csv"))[0].read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 9, len(lines)   # 表头 + 4 触发 + 4 恢复
    print("alarm ok:", book.total, "条")


if __name__ == "__main__":
    test_protocol()
    test_reader()
    test_csv()
    test_header_migration()
    test_alarm()
    print("ALL OK")
