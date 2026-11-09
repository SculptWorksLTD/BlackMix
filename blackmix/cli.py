"""Uso da terminale:  python3 -m blackmix.cli "Canzone.logicx" [-o mix.wav] [--genere Rock]"""
from __future__ import annotations

import argparse
import os
import sys
import time

from .mixer import MixSettings, analyze, render
from .project import scan
from .roles import GENRES, ROLES


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mix automatico di un progetto Logic o di una cartella di tracce")
    ap.add_argument("progetto", help="file .logicx oppure cartella con i WAV/AIFF")
    ap.add_argument("-o", "--out", help="file WAV di uscita")
    ap.add_argument("--genere", default="Pop", choices=list(GENRES))
    ap.add_argument("--lufs", type=float, default=-14.0)
    ap.add_argument("--voce", type=float, default=0.0, help="dB in piu' o in meno sulla voce")
    ap.add_argument("--riverbero", type=float, default=1.0)
    ap.add_argument("--compressione", type=float, default=1.0)
    ap.add_argument("--elenca", action="store_true", help="mostra solo le tracce trovate")
    a = ap.parse_args(argv)

    def prog(f, m):
        sys.stderr.write(f"\r[{f * 100:5.1f}%] {m:<60.60}")
        sys.stderr.flush()

    tracks, sr = scan(a.progetto)
    if not tracks:
        print("Nessun file audio trovato.")
        return 1
    analyze(tracks, sr, prog)
    sys.stderr.write("\n")
    for t in tracks:
        mark = "x" if t.include else " "
        role = ROLES.get(t.role).label if t.info else "-"
        print(f"[{mark}] {t.name:<32.32} {role:<22} inizio {t.offset:7.2f}s  {'; '.join(t.notes)}")
    if a.elenca:
        return 0
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.progetto.rstrip('/'))),
                                f"BlackMix_{time.strftime('%Y%m%d_%H%M%S')}.wav")
    s = MixSettings(genre=a.genere, vocal_db=a.voce, reverb=a.riverbero,
                    compression=a.compressione, target_lufs=a.lufs)
    t0 = time.time()
    res = render(tracks, sr, s, out, prog)
    sys.stderr.write("\n")
    print(res["report"])
    print(f"Mix salvato in: {res['path']}  ({time.time() - t0:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
