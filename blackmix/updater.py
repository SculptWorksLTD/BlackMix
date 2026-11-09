"""Aggiornamento automatico tramite le release di GitHub.

All'avvio l'app chiede a GitHub qual e' l'ultima release. Se e' piu' nuova della
versione installata scarica il .pkg adatto al Mac (arm64 / x86_64) e lo apre con
l'Installer di macOS.
"""
from __future__ import annotations

import json
import os
import platform
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request

from . import __version__

REPO = "SculptWorksLTD/BlackMix"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
UA = f"BlackMix/{__version__}"


def _ssl_context():
    try:
        import certifi  # incluso nel pacchetto .pkg
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_version(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_bundled() -> bool:
    """True se l'app gira dal pacchetto installato (BlackMix.app)."""
    return ".app/Contents/" in os.path.abspath(sys.executable)


def check(timeout: float = 8.0) -> dict | None:
    """Restituisce info sulla nuova versione, oppure None se non ce n'e'."""
    req = urllib.request.Request(API_LATEST, headers={
        "User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        data = json.load(r)
    tag = data.get("tag_name", "")
    if parse_version(tag) <= parse_version(__version__):
        return None
    arch = platform.machine().lower()  # 'arm64' o 'x86_64'
    asset = None
    for a in data.get("assets", []):
        n = a.get("name", "").lower()
        if n.endswith(".pkg") and arch in n:
            asset = a
            break
    if asset is None:
        pkgs = [a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".pkg")]
        asset = pkgs[0] if len(pkgs) == 1 else None
    return dict(version=tag.lstrip("vV"), notes=(data.get("body") or "").strip(),
                url=asset["browser_download_url"] if asset else None,
                size=asset.get("size") if asset else None,
                name=asset["name"] if asset else None,
                page=data.get("html_url") or RELEASES_PAGE)


def download(info: dict, progress=None) -> str:
    """Scarica il .pkg in una cartella temporanea e restituisce il percorso."""
    if not info.get("url"):
        raise RuntimeError("Nella release non c'e' un pacchetto per questo Mac.")
    d = tempfile.mkdtemp(prefix="blackmix-update-")
    path = os.path.join(d, info["name"])
    req = urllib.request.Request(info["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r, open(path, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with open(path, "rb") as f:
        if f.read(4) != b"xar!":
            raise RuntimeError("Il file scaricato non e' un pacchetto valido.")
    return path


def open_installer(pkg_path: str) -> None:
    subprocess.Popen(["open", pkg_path])
