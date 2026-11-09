"""Ricerca delle tracce audio dentro un progetto Logic (.logicx) o una cartella."""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .audio_io import AUDIO_EXTS, AudioFormatError, AudioInfo, probe

# cartelle che non contengono registrazioni da mixare
SKIP_DIRS = {"bounces", "freeze files", "undo data", "autosave", "alternatives", "resources",
             "project file backups", "backup", "backups", "samples", "sampler files",
             "impulse responses", "video files", "blackmix"}


@dataclass
class TrackFile:
    path: str
    name: str                 # nome file senza estensione
    base: str                 # nome traccia (senza #01, #02 delle take)
    info: AudioInfo | None
    error: str | None = None
    include: bool = True
    role: str = "altro"
    role_source: str = ""     # 'nome' / 'suono' / 'utente'
    offset: float = 0.0       # secondi dall'inizio del mix
    has_timestamp: bool = False
    adjust_db: float = 0.0    # correzione di volume scelta dall'utente
    notes: list = field(default_factory=list)
    analysis: dict = field(default_factory=dict)


def _audio_dirs(path: str) -> list[str]:
    path = os.path.abspath(path.rstrip("/"))
    dirs = []
    if path.lower().endswith(".logicx"):
        dirs.append(os.path.join(path, "Media", "Audio Files"))
        dirs.append(os.path.join(path, "Media"))
        parent = os.path.dirname(path)
        # progetto salvato come "cartella": Canzone/Canzone.logicx + Canzone/Audio Files
        dirs.append(os.path.join(parent, "Audio Files"))
    else:
        dirs.append(path)
        # cartella che contiene un progetto .logicx con i file dentro il pacchetto
        try:
            for e in os.listdir(path):
                if e.lower().endswith(".logicx"):
                    dirs.append(os.path.join(path, e, "Media"))
        except OSError:
            pass
    seen, out = set(), []
    for d in dirs:
        if os.path.isdir(d) and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_audio_files(path: str) -> list[str]:
    found = []
    seen = set()
    for root_dir in _audio_dirs(path):
        for root, dirnames, files in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in SKIP_DIRS and not d.startswith(".")
                           and not d.lower().endswith(".logicx")]
            for fn in files:
                if fn.startswith("."):
                    continue
                if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                    p = os.path.realpath(os.path.join(root, fn))
                    if p not in seen:
                        seen.add(p)
                        found.append(p)
    return sorted(found, key=lambda p: os.path.basename(p).lower())


_TAKE_RE = re.compile(r"^(.*?)[ _]*#(\d+)$")


def _split_take(stem: str) -> tuple[str, int | None]:
    m = _TAKE_RE.match(stem)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return stem, None


def scan(path: str) -> tuple[list[TrackFile], int]:
    """Trova i file, legge le intestazioni, allinea con i timestamp.
    Restituisce (tracce, sample rate del progetto)."""
    files = find_audio_files(path)
    tracks: list[TrackFile] = []
    for p in files:
        stem = os.path.splitext(os.path.basename(p))[0]
        base, _take = _split_take(stem)
        try:
            info = probe(p)
            t = TrackFile(p, stem, base, info)
            if info.frames < info.sr * 0.5:
                t.include = False
                t.notes.append("troppo corto")
        except (AudioFormatError, OSError) as e:
            t = TrackFile(p, stem, base, None, error=str(e), include=False)
            t.notes.append("illeggibile")
        tracks.append(t)

    ok = [t for t in tracks if t.info]
    if not ok:
        return tracks, 44100
    sr = Counter(t.info.sr for t in ok).most_common(1)[0][0]

    # take multiple della stessa traccia: tengo l'ultima, le altre deselezionate
    groups = defaultdict(list)
    for t in ok:
        groups[t.base].append(t)
    for base, items in groups.items():
        takes = [(_split_take(t.name)[1] or 0, t) for t in items]
        if len(takes) > 1 and any(n for n, _ in takes):
            takes.sort(key=lambda x: x[0])
            for n, t in takes[:-1]:
                t.include = False
                t.notes.append("take precedente")
            takes[-1][1].notes.append(f"ultima di {len(takes)} take")

    # allineamento: timestamp BWF relativi al piu' piccolo
    stamped = [t for t in ok if t.info.time_ref is not None]
    if stamped:
        t0 = min(t.info.time_ref / t.info.sr for t in stamped)
        for t in stamped:
            t.offset = round(t.info.time_ref / t.info.sr - t0, 4)
            t.has_timestamp = True
    for t in ok:
        if not t.has_timestamp:
            t.notes.append("senza timestamp: parte da 0")
        if t.info.sr != sr:
            t.notes.append(f"{t.info.sr} Hz, verra' convertito")
    return tracks, sr
