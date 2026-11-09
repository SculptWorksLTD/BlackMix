"""Lettura e scrittura di file audio senza librerie esterne (solo numpy).

Supporta:
  - WAV / BWF: PCM 16/24/32 bit, float 32/64, WAVE_FORMAT_EXTENSIBLE
  - AIFF / AIFF-C: PCM big-endian, 'sowt' little-endian, float 'fl32'/'fl64'
  - qualunque altro formato (CAF, MP3, ...) tramite ffmpeg, se installato

Dai file BWF legge il timestamp (TimeReference del chunk 'bext'):
Logic lo scrive nelle registrazioni ed indica la posizione nel progetto
in cui la registrazione e' iniziata. Serve per allineare le tracce.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np

AUDIO_EXTS = {".wav", ".wave", ".bwf", ".aif", ".aiff", ".aifc", ".caf"}


@dataclass
class AudioInfo:
    path: str
    sr: int
    channels: int
    frames: int
    time_ref: int | None  # campioni, dal chunk bext (None se assente)
    fmt: str  # descrizione breve

    @property
    def duration(self) -> float:
        return self.frames / float(self.sr) if self.sr else 0.0


class AudioFormatError(Exception):
    pass


# --------------------------------------------------------------------------
# Utilita'
# --------------------------------------------------------------------------

def _ext80_to_float(b: bytes) -> float:
    """Decodifica il float a 80 bit (IEEE extended) usato da AIFF per il sample rate."""
    exp = ((b[0] << 8) | b[1]) & 0x7FFF
    mant = int.from_bytes(b[2:10], "big")
    if exp == 0 and mant == 0:
        return 0.0
    sign = -1.0 if b[0] & 0x80 else 1.0
    return sign * mant * 2.0 ** (exp - 16383 - 63)


def _iter_chunks(f, start: int, end: int, big_endian: bool):
    pos = start
    fmt = ">4sI" if big_endian else "<4sI"
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid, size = struct.unpack(fmt, hdr)
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def _ffmpeg_path() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.exists(cand):
            return cand
    return None


# --------------------------------------------------------------------------
# Lettura intestazioni
# --------------------------------------------------------------------------

def _probe_wav(path: str) -> tuple[AudioInfo, dict]:
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
            raise AudioFormatError("non e' un WAV")
        rf64 = head[:4] == b"RF64"
        file_size = os.path.getsize(path)
        fmt = None
        data_off = data_size = None
        time_ref = None
        ds64_data = None
        for cid, off, size in _iter_chunks(f, 12, file_size, False):
            if cid == b"ds64":
                f.seek(off)
                d = f.read(min(size, 28))
                if len(d) >= 16:
                    ds64_data = struct.unpack("<Q", d[8:16])[0]
            elif cid == b"fmt ":
                f.seek(off)
                d = f.read(size)
                tag, ch, sr, _br, block, bits = struct.unpack("<HHIIHH", d[:16])
                if tag == 0xFFFE and len(d) >= 26:
                    tag = struct.unpack("<H", d[24:26])[0]
                fmt = (tag, ch, sr, block, bits)
            elif cid == b"bext":
                f.seek(off)
                d = f.read(min(size, 346))
                if len(d) >= 346:
                    lo, hi = struct.unpack("<II", d[338:346])
                    time_ref = (hi << 32) | lo
            elif cid == b"data":
                data_off = off
                data_size = size
                if rf64 and size == 0xFFFFFFFF and ds64_data is not None:
                    data_size = ds64_data
                # se 'data' e' l'ultimo chunk la dimensione puo' essere sbagliata
                data_size = min(data_size, file_size - off)
                if rf64:
                    break
    if fmt is None or data_off is None:
        raise AudioFormatError("WAV senza chunk fmt/data")
    tag, ch, sr, block, bits = fmt
    if tag == 1 and bits in (8, 16, 24, 32):
        kind = "int"
    elif tag == 3 and bits in (32, 64):
        kind = "float"
    else:
        raise AudioFormatError(f"formato WAV non supportato (tag {tag}, {bits} bit)")
    bps = bits // 8
    frames = data_size // (bps * ch)
    info = AudioInfo(path, sr, ch, frames, time_ref, f"WAV {bits} bit")
    return info, dict(off=data_off, kind=kind, bps=bps, endian="<", unsigned8=True)


def _probe_aiff(path: str) -> tuple[AudioInfo, dict]:
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"FORM" or head[8:12] not in (b"AIFF", b"AIFC"):
            raise AudioFormatError("non e' un AIFF")
        aifc = head[8:12] == b"AIFC"
        file_size = os.path.getsize(path)
        comm = None
        data_off = None
        for cid, off, size in _iter_chunks(f, 12, file_size, True):
            if cid == b"COMM":
                f.seek(off)
                d = f.read(size)
                ch, frames, bits = struct.unpack(">hIh", d[:8])
                sr = _ext80_to_float(d[8:18])
                comp = d[18:22] if aifc and len(d) >= 22 else b"NONE"
                comm = (ch, frames, bits, int(round(sr)), comp)
            elif cid == b"SSND":
                f.seek(off)
                ssnd_off, _bs = struct.unpack(">II", f.read(8))
                data_off = off + 8 + ssnd_off
    if comm is None or data_off is None:
        raise AudioFormatError("AIFF senza COMM/SSND")
    ch, frames, bits, sr, comp = comm
    comp = comp.lower()
    if comp in (b"none", b"twos"):
        kind, endian = "int", ">"
    elif comp == b"sowt":
        kind, endian = "int", "<"
    elif comp in (b"fl32", b"fl64"):
        kind, endian = "float", ">"
        bits = 32 if comp == b"fl32" else 64
    else:
        raise AudioFormatError(f"compressione AIFF non supportata: {comp!r}")
    if kind == "int" and bits not in (8, 16, 24, 32):
        raise AudioFormatError(f"AIFF a {bits} bit non supportato")
    bps = (bits + 7) // 8
    # non fidarsi ciecamente di frames: verifica con la dimensione del file
    max_frames = max(0, (file_size - data_off) // (bps * ch))
    frames = min(frames, max_frames)
    info = AudioInfo(path, sr, ch, frames, None, f"AIFF {bits} bit")
    return info, dict(off=data_off, kind=kind, bps=bps, endian=endian, unsigned8=False)


def _probe_native(path: str):
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic in (b"RIFF", b"RF64"):
        return _probe_wav(path)
    if magic == b"FORM":
        return _probe_aiff(path)
    raise AudioFormatError("formato non riconosciuto")


def probe(path: str) -> AudioInfo:
    """Legge solo le intestazioni del file."""
    try:
        return _probe_native(path)[0]
    except AudioFormatError:
        ff = _ffmpeg_path()
        if not ff:
            raise
        info = _ffprobe_via_ffmpeg(ff, path)
        if info is None:
            raise
        return info


def _ffprobe_via_ffmpeg(ff: str, path: str) -> AudioInfo | None:
    probe_bin = os.path.join(os.path.dirname(ff), "ffprobe")
    if not os.path.exists(probe_bin):
        probe_bin = shutil.which("ffprobe")
    if not probe_bin:
        return None
    try:
        out = subprocess.run(
            [probe_bin, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate,channels,duration_ts,duration", "-of", "default=nw=1", path],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    vals = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    try:
        sr = int(vals["sample_rate"])
        ch = int(vals["channels"])
        dur = float(vals.get("duration", "0") or 0)
    except (KeyError, ValueError):
        return None
    return AudioInfo(path, sr, ch, int(dur * sr), None, "via ffmpeg")


# --------------------------------------------------------------------------
# Lettura dati
# --------------------------------------------------------------------------

def _decode(raw: bytes, ch: int, kind: str, bps: int, endian: str, unsigned8: bool) -> np.ndarray:
    if kind == "float":
        dt = np.dtype(endian + ("f4" if bps == 4 else "f8"))
        x = np.frombuffer(raw, dtype=dt).astype(np.float32)
    elif bps == 1:
        if unsigned8:
            x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            x = np.frombuffer(raw, dtype=np.int8).astype(np.float32) / 128.0
    elif bps == 2:
        x = np.frombuffer(raw, dtype=np.dtype(endian + "i2")).astype(np.float32) / 32768.0
    elif bps == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        if endian == "<":
            i = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        else:
            i = b[:, 2] | (b[:, 1] << 8) | (b[:, 0] << 16)
        i = (i << 8) >> 8  # estensione del segno
        x = i.astype(np.float32) / 8388608.0
    elif bps == 4:
        x = (np.frombuffer(raw, dtype=np.dtype(endian + "i4")).astype(np.float64) / 2147483648.0).astype(np.float32)
    else:
        raise AudioFormatError("profondita' non supportata")
    n = (x.size // ch) * ch
    return x[:n].reshape(-1, ch)


def load(path: str) -> tuple[np.ndarray, int]:
    """Restituisce (dati float32 di forma (campioni, canali), sample rate)."""
    try:
        info, p = _probe_native(path)
    except AudioFormatError:
        return _load_via_ffmpeg(path)
    nbytes = info.frames * info.channels * p["bps"]
    with open(path, "rb") as f:
        f.seek(p["off"])
        raw = f.read(nbytes)
    data = _decode(raw, info.channels, p["kind"], p["bps"], p["endian"], p["unsigned8"])
    return np.ascontiguousarray(data), info.sr


def _load_via_ffmpeg(path: str) -> tuple[np.ndarray, int]:
    ff = _ffmpeg_path()
    if not ff:
        raise AudioFormatError(
            f"Formato non leggibile senza ffmpeg: {os.path.basename(path)}")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "conv.wav")
        r = subprocess.run([ff, "-v", "error", "-y", "-i", path, "-c:a", "pcm_f32le", out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AudioFormatError(f"ffmpeg non riesce a leggere il file: {r.stderr.strip()[:200]}")
        info, p = _probe_wav(out)
        with open(out, "rb") as f:
            f.seek(p["off"])
            raw = f.read(info.frames * info.channels * p["bps"])
        return _decode(raw, info.channels, p["kind"], p["bps"], "<", True).copy(), info.sr


# --------------------------------------------------------------------------
# Scrittura
# --------------------------------------------------------------------------

def write_wav(path: str, data: np.ndarray, sr: int, bits: int = 24) -> None:
    """Scrive un WAV PCM (16 o 24 bit). data: (campioni, canali) float."""
    if data.ndim == 1:
        data = data[:, None]
    ch = data.shape[1]
    x = np.clip(np.nan_to_num(data.astype(np.float64)), -1.0, 1.0)
    if bits == 16:
        # dither TPDF leggero
        rng = np.random.default_rng(1)
        d = (rng.random(x.shape) - rng.random(x.shape)) / 32768.0
        i = np.clip(np.round((x + d) * 32767.0), -32768, 32767).astype("<i2")
        raw = i.tobytes()
    elif bits == 24:
        i = np.clip(np.round(x * 8388607.0), -8388608, 8388607).astype("<i4")
        raw = i.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    else:
        raise ValueError("bits deve essere 16 o 24")
    bps = bits // 8
    data_size = len(raw)
    hdr = b"RIFF" + struct.pack("<I", 36 + data_size + (data_size & 1)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * ch * bps, ch * bps, bits)
    hdr += b"data" + struct.pack("<I", data_size)
    with open(path, "wb") as f:
        f.write(hdr)
        f.write(raw)
        if data_size & 1:
            f.write(b"\x00")
