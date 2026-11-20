# -*- coding: utf-8 -*-
"""在线更新模块自检：本地 HTTP 服务 + 清单/下载/校验/脚本生成。"""
import http.server
import json
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import updater


def test_versions():
    assert updater.parse_version("2026.11.19.4") == (2026, 11, 19, 4)
    assert updater.is_newer("2026.11.20.1", "2026.11.19.4")
    assert not updater.is_newer("2026.11.19.4", "2026.11.19.4")
    assert not updater.is_newer("2026.11.18.9", "2026.11.19.4")
    print("versions ok")


def test_http_flow():
    folder = Path(tempfile.mkdtemp(prefix="upd_"))
    payload = b"NEW-EXE-" + bytes(range(256)) * 50
    (folder / "new.exe").write_bytes(payload)
    manifest = {
        "version": "2026.11.20.1",
        "notes": "测试",
        "exe_url": "/new.exe",
        "exe_sha256": updater.sha256_file(folder / "new.exe"),
    }
    (folder / "update.json").write_text(json.dumps(manifest), encoding="utf-8")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(folder), **kw)

        def log_message(self, *a):
            pass

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        m = updater.fetch_manifest(base + "/update.json", timeout=5)
        assert m["version"] == "2026.11.20.1"
        dest = folder / "downloaded.exe"
        got = []
        updater.download_file(base + "/new.exe", dest, timeout=5,
                              progress=lambda d, t: got.append((d, t)))
        assert dest.read_bytes() == payload
        assert updater.sha256_file(dest) == manifest["exe_sha256"]
        assert got and got[-1][0] == len(payload)
        print("http ok")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_script():
    d = Path(tempfile.mkdtemp(prefix="upd_scr_"))
    new = d / "new one.exe"
    target = d / "目标 程序.exe"
    new.write_bytes(b"x")
    target.write_bytes(b"y")
    script = updater.make_update_script(new, target, 12345)
    text = script.read_text(encoding="utf-8-sig")
    assert "12345" in text and target.name in text and new.name in text
    assert "Copy-Item" in text and "Start-Process" in text
    print("script ok")


if __name__ == "__main__":
    test_versions()
    test_http_flow()
    test_script()
    print("ALL OK")
