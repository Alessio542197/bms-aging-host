# -*- coding: utf-8 -*-
"""在线更新：读取清单、下载、校验、替换 EXE 并重启。"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


def _opener(proxy: str = ""):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


def friendly_error(exc: Exception, url: str) -> str:
    """把常见网络错误翻译成容易处理的提示。"""
    host = urllib.parse.urlsplit(url).hostname or url
    reason = getattr(exc, "reason", None)
    if isinstance(exc, urllib.error.URLError) and isinstance(reason, socket.gaierror):
        return ("无法解析域名 %s（DNS 失败）。请检查网址是否写错，"
                "或换一个能访问的清单地址（Gitee、公司服务器、对象存储等）。" % host)
    if isinstance(exc, urllib.error.HTTPError):
        return ("服务器返回 HTTP %s（%s）。请确认清单地址存在，"
                "且仓库/文件是公开可访问的。" % (exc.code, host))
    if isinstance(exc, socket.timeout) or "timed out" in str(exc).lower():
        return "连接 %s 超时。请检查网络，或在 config.json 的 update.proxy 填本机代理地址。" % host
    return "检查更新失败：%s" % exc


def parse_version(text: str) -> Tuple[int, ...]:
    parts = []
    for piece in str(text).strip().replace("-", ".").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def fetch_manifest(url: str, timeout: float = 15.0, proxy: str = "") -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "BmsAgingHost-Updater"})
    with _opener(proxy).open(req, timeout=timeout) as resp:
        data = resp.read()
    obj = json.loads(data.decode("utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError("更新清单格式不正确")
    return obj


def download_file(url: str, dest: Path, timeout: float = 30.0,
                  progress: Optional[Callable[[int, int], None]] = None,
                  proxy: str = "") -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "BmsAgingHost-Updater"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    done = 0
    with _opener(proxy).open(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if total and done != total:
        raise IOError("下载不完整")
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_update_script(new_exe: Path, target_exe: Path, pid: int) -> Path:
    """生成等待旧进程退出、替换 exe 并重启的 PowerShell 脚本。"""

    def q(p: Path) -> str:
        return str(p).replace("'", "''")

    script = f"""$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 2
try {{ Wait-Process -Id {pid} -Timeout 20 }} catch {{}}
try {{ Stop-Process -Id {pid} -Force }} catch {{}}
for ($i = 0; $i -lt 60; $i++) {{
    try {{
        Copy-Item -LiteralPath '{q(new_exe)}' -Destination '{q(target_exe)}' -Force -ErrorAction Stop
        break
    }} catch {{
        Start-Sleep -Milliseconds 500
    }}
}}
try {{ Start-Process -FilePath '{q(target_exe)}' }} catch {{}}
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force
"""
    path = Path(tempfile.gettempdir()) / f"bms_update_{pid}.ps1"
    path.write_text(script, encoding="utf-8-sig")
    return path


def launch_update_script(script: Path) -> None:
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", str(script)],
        creationflags=flags,
        close_fds=True,
    )


def launcher_path() -> Optional[Path]:
    raw = os.environ.get("BMS_LAUNCHER_EXE", "")
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None
