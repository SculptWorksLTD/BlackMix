"""Il cuore del mix automatico: analisi, decisioni, elaborazione, report."""
from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass
from math import gcd

import numpy as np
from scipy import signal

from . import dsp
from .audio_io import load, write_wav
from .project import TrackFile
from .roles import GENRES, ROLES, guess_from_features, guess_from_name, side_hint

ANCHOR_LUFS = -20.0  # loudness di riferimento della voce prima del master
POLARITY_ROLES = {"cassa", "rullante", "tom", "basso"}
_BAND_CENTERS = 1000.0 * 2.0 ** (np.arange(-15, 13) / 3.0)  # 31.5 Hz .. 16 kHz


@dataclass
class MixSettings:
    genre: str = "Pop"
    vocal_db: float = 0.0         # voce piu' avanti (+) o piu' dentro (-)
    reverb: float = 1.0           # 0 = niente, 1 = normale, 2 = tanto
    compression: float = 1.0      # 0 = niente, 1 = normale, 1.5 = tanta
    target_lufs: float = -14.0
    ceiling_db: float = -1.0
    bits: int = 24


def _noop(frac, msg):
    pass


# --------------------------------------------------------------------------
# Caricamento
# --------------------------------------------------------------------------

def load_track(t: TrackFile, sr: int) -> np.ndarray:
    x, fsr = load(t.path)
    if fsr != sr:
        g = gcd(int(fsr), int(sr))
        x = signal.resample_poly(x, sr // g, fsr // g, axis=0).astype(np.float32)
    if x.shape[1] > 2:
        x = x[:, :2]
    return x


# --------------------------------------------------------------------------
# Analisi
# --------------------------------------------------------------------------

def analyze(tracks: list[TrackFile], sr: int, progress=_noop) -> None:
    todo = [t for t in tracks if t.info]
    for k, t in enumerate(todo):
        progress(k / max(1, len(todo)), f"Analizzo {t.name}")
        try:
            x = load_track(t, sr)
        except Exception as e:  # file rovinato o non leggibile
            t.error = str(e)
            t.include = False
            t.notes.append("illeggibile")
            continue
        t.analysis = _analyze_signal(x, sr)
        if t.analysis["silent"]:
            t.include = False
            t.notes.append("vuota/silenziosa")
        if t.role_source != "utente":
            r = guess_from_name(t.base) or guess_from_name(t.name)
            if r:
                t.role, t.role_source = r, "nome"
            else:
                t.role, t.role_source = guess_from_features(t.analysis), "suono"
    progress(1.0, "Analisi completata")


def _analyze_signal(x: np.ndarray, sr: int) -> dict:
    lufs = dsp.integrated_loudness(x, sr)
    peak = float(np.abs(x).max()) if x.size else 0.0
    res = dict(lufs=lufs, peak_db=float(dsp.lin2db(peak)), channels=x.shape[1],
               silent=(not np.isfinite(lufs)) or lufs < -60.0)
    if res["silent"]:
        return res
    mono = x.mean(axis=1)
    # se i due canali sono praticamente uguali lo tratto come mono
    if x.shape[1] == 2:
        d = np.abs(x[:, 0] - x[:, 1]).max()
        res["dual_mono"] = bool(d < 1e-4)
    f, p = signal.welch(mono, sr, nperseg=8192, noverlap=4096)
    m = (f >= 20) & (f <= 20000)
    tot = p[m].sum() + 1e-20
    res["centroid"] = float((f[m] * p[m]).sum() / tot)
    res["low_ratio"] = float(p[(f >= 20) & (f < 150)].sum() / tot)
    bands = []
    for fc in _BAND_CENTERS:
        lo, hi = fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)
        bands.append(10 * np.log10(p[(f >= lo) & (f < hi)].sum() + 1e-20))
    res["bands"] = np.array(bands) - 10 * np.log10(tot)  # relativo al totale
    lev, _ = dsp.detector_levels(x, sr, 10.0)
    act = lev > lev.max() - 40
    rms_db = 10 * np.log10(np.mean(10 ** (lev[act] / 10)))
    res["crest_db"] = float(res["peak_db"] - rms_db)
    # inviluppo a bassa frequenza per il controllo di polarita'
    lp = signal.sosfilt(dsp.lowpass(700.0, sr, 4), mono)
    step = max(1, sr // 2000)
    res["lp"] = lp[::step].astype(np.float32)
    res["lp_sr"] = sr / step
    return res


# --------------------------------------------------------------------------
# Decisioni
# --------------------------------------------------------------------------

def _spread(n, width):
    if n == 1:
        return [0.0]
    return list(np.linspace(-width, width, n))


def plan(tracks: list[TrackFile], settings: MixSettings) -> dict:
    act = [t for t in tracks if t.include and t.info and t.analysis and not t.analysis.get("silent")]
    genre = GENRES.get(settings.genre, GENRES["Pop"])
    groups = defaultdict(list)
    for t in act:
        groups[t.role].append(t)

    plans = {}
    for role_key, items in groups.items():
        role = ROLES.get(role_key, ROLES["altro"])
        n = len(items)
        # pan
        mode = role.pan
        if isinstance(mode, (int, float)):
            pans = [float(mode)] * n
        elif mode == "spread":
            pans = _spread(n, 0.6 if n <= 3 else 0.75)
        elif mode == "spread_wide":
            pans = _spread(n, 0.9)
        elif mode == "side":
            pans = [-0.3] if n == 1 else ([-0.8, 0.8] if n == 2 else _spread(n, 0.85))
        else:
            pans = [0.0] * n
        for t, p in zip(items, pans):
            h = side_hint(t.name)
            if h is not None:
                p = h * max(abs(p), 0.5)
            stereo = t.analysis["channels"] > 1 and not t.analysis.get("dual_mono")
            if stereo and n == 1:
                p = 0.0 if not isinstance(mode, (int, float)) else p
            level = (ANCHOR_LUFS + role.level + genre["levels"].get(role_key, 0.0)
                     - 10 * math.log10(n) + t.adjust_db)
            if role_key == "voce":
                level += settings.vocal_db
            elif role_key == "cori":
                level += settings.vocal_db * 0.5
            ratio, att, rel, gr = role.comp
            gr *= settings.compression * genre["comp"]
            send = None
            if role.reverb is not None and settings.reverb > 0.01:
                send = role.reverb + genre["reverb"] + 20 * math.log10(settings.reverb)
            plans[t.path] = dict(
                role=role_key, target=level, pan=float(np.clip(p, -1, 1)), hpf=role.hpf,
                eq=[dict(b) for b in role.eq], unmask=[], comp=(ratio, att, rel, gr),
                deess=role.deess, send=send, invert=False, stereo=stereo)

    _unmask(act, plans)
    _polarity(groups, plans)
    return plans


def _unmask(act, plans):
    """EQ anti-mascheramento: le tracce meno importanti cedono spazio nelle zone chiave
    di quelle piu' importanti (voce, cassa, basso...)."""
    E = {}
    for t in act:
        p = plans[t.path]
        # livello per banda nel mix ~ spettro relativo + loudness obiettivo
        E[t.path] = t.analysis["bands"] + p["target"]
    cands = defaultdict(list)
    for ti in act:
        ri = ROLES[plans[ti.path]["role"]]
        if not ri.key_bands:
            continue
        Ei = E[ti.path]
        for b, fc in enumerate(_BAND_CENTERS):
            if not any(lo <= fc <= hi for lo, hi in ri.key_bands):
                continue
            if Ei[b] < Ei.max() - 18:
                continue
            for tj in act:
                if tj is ti:
                    continue
                pj = plans[tj.path]
                rj = ROLES[pj["role"]]
                if rj.priority >= ri.priority or rj.key == ri.key:
                    continue
                if pj["hpf"] and fc < pj["hpf"] * 1.3:
                    continue
                Ej = E[tj.path]
                if Ej[b] < Ej.max() - 15:
                    continue
                margin = Ej[b] - (Ei[b] - 8.0)
                if margin <= 0:
                    continue
                # piu' e' grande la differenza di importanza, piu' si taglia
                weight = min(1.0, 0.4 + 0.15 * (ri.priority - rj.priority))
                cap = 2.5 if fc < 500 else 3.5
                cut = float(np.clip(margin * 0.35 * weight, 0.0, cap))
                if cut < 0.8:
                    continue
                cands[tj.path].append((b, cut, ri.label))
    for path, lst in cands.items():
        # per ogni banda tengo il taglio maggiore, poi unisco le bande vicine
        best = {}
        for b, cut, who in lst:
            if b not in best or cut > best[b][0]:
                best[b] = (cut, who)
        bs = sorted(best)
        clusters, cur = [], [bs[0]]
        for b in bs[1:]:
            if b - cur[-1] <= 1:
                cur.append(b)
            else:
                clusters.append(cur)
                cur = [b]
        clusters.append(cur)
        scored = sorted(clusters, key=lambda c: -max(best[b][0] for b in c))[:3]
        for c in scored:
            fc = float(np.exp(np.mean(np.log(_BAND_CENTERS[c]))))
            cut = max(best[b][0] for b in c)
            q = 1.6 if len(c) == 1 else (1.1 if len(c) == 2 else 0.8)
            who = best[max(c, key=lambda b: best[b][0])][1]
            plans[path]["unmask"].append(dict(type="peak", f=round(fc), g=-round(cut, 1), q=q, why=who))


def _polarity(groups, plans):
    """Se due microfoni della stessa sorgente (es. cassa dentro/fuori) sono in
    controfase, inverte la polarita' di uno dei due."""
    for role_key, items in groups.items():
        if role_key not in POLARITY_ROLES or len(items) < 2:
            continue
        ref = max(items, key=lambda t: t.analysis["lufs"])
        a = ref.analysis["lp"]
        fs = ref.analysis["lp_sr"]
        for t in items:
            if t is ref:
                continue
            b = t.analysis["lp"]
            # posizionamento nel tempo comune
            da = int(round(ref.offset * fs))
            db_ = int(round(t.offset * fs))
            start = max(da, db_)
            end = min(da + a.size, db_ + b.size)
            if end - start < fs * 2:
                continue
            xa = a[start - da: end - da].astype(np.float64)
            xb = b[start - db_: end - db_].astype(np.float64)
            maxlag = int(fs * 0.004)
            best, bestlag = 0.0, 0
            na = np.sqrt((xa ** 2).sum()) + 1e-12
            nb = np.sqrt((xb ** 2).sum()) + 1e-12
            for lag in range(-maxlag, maxlag + 1):
                if lag >= 0:
                    c = np.dot(xa[lag:], xb[: xb.size - lag])
                else:
                    c = np.dot(xa[: xa.size + lag], xb[-lag:])
                c /= na * nb
                if abs(c) > abs(best):
                    best, bestlag = c, lag
            if best < -0.3:
                plans[t.path]["invert"] = True


# --------------------------------------------------------------------------
# Elaborazione
# --------------------------------------------------------------------------

def render(tracks: list[TrackFile], sr: int, settings: MixSettings, out_path: str,
           progress=_noop) -> dict:
    plans = plan(tracks, settings)
    act = [t for t in tracks if t.path in plans]
    if not act:
        raise ValueError("Nessuna traccia selezionata da mixare.")

    lengths = {t.path: int(round(t.info.frames * sr / t.info.sr)) for t in act}
    total = max(int(round(t.offset * sr)) + lengths[t.path] for t in act)
    tail = int(sr * 3.0)
    mix = np.zeros((total + tail, 2), dtype=np.float32)
    send = np.zeros(total + tail, dtype=np.float32)
    any_send = False
    results = []

    for k, t in enumerate(act):
        p = plans[t.path]
        progress(0.05 + 0.75 * k / len(act), f"Elaboro {t.name}")
        x = load_track(t, sr)
        if p["stereo"] is False and x.shape[1] == 2:
            x = x.mean(axis=1, keepdims=True)
        if p["invert"]:
            x = -x
        x = dsp.eq_chain(x, sr, p["hpf"], p["eq"] + p["unmask"])
        ratio, att, rel, want_gr = p["comp"]
        comp_info = None
        if want_gr > 0.2 and ratio > 1.0:
            lev, _ = dsp.detector_levels(x, sr, 1.0)
            a = lev[lev > lev.max() - 40]
            if a.size:
                thr = float(np.percentile(a, 95) - want_gr * ratio / (ratio - 1.0))
                x, mean_gr, max_gr = dsp.compressor(x, sr, thr, ratio, att, rel)
                comp_info = dict(thr=thr, ratio=ratio, att=att, rel=rel, mean_gr=mean_gr, max_gr=max_gr)
        deess_red = 0.0
        if p["deess"]:
            x, deess_red = dsp.deesser(x, sr)
        L = dsp.integrated_loudness(x, sr)
        if not np.isfinite(L):
            continue
        fader = p["target"] - L
        x *= float(dsp.db2lin(fader))
        st = dsp.pan_to_stereo(x, p["pan"])
        o = int(round(t.offset * sr))
        n = min(st.shape[0], mix.shape[0] - o)
        mix[o:o + n] += st[:n]
        if p["send"] is not None:
            send[o:o + n] += st[:n].mean(axis=1) * float(dsp.db2lin(p["send"]))
            any_send = True
        results.append(dict(track=t, plan=p, fader=fader, comp=comp_info, deess=deess_red,
                            peak_in=t.analysis.get("peak_db")))
        del x, st

    if any_send:
        progress(0.82, "Riverbero")
        rt = {"Rock": 1.5, "Jazz / live": 1.6, "Acustico / cantautore": 2.0}.get(settings.genre, 1.8)
        ir = dsp.make_reverb_ir(sr, rt60=rt)
        wet = dsp.convolve_reverb(send, ir)
        mix += wet[: mix.shape[0]]
        del wet
    del send

    progress(0.88, "Master")
    mix = dsp.apply_sos(mix, dsp.highpass(25.0, sr, 2))
    st_lev = dsp.short_term_levels(mix, sr)
    glue = None
    if st_lev.size:
        lev, _ = dsp.detector_levels(mix, sr, 1.0)
        a = lev[lev > lev.max() - 40]
        thr = float(np.percentile(a, 90) - 3.0)
        mix, gmean, gmax = dsp.compressor(mix, sr, thr, 1.8, 30.0, 250.0, knee_db=8.0)
        glue = dict(thr=thr, mean_gr=gmean, max_gr=gmax)

    L = dsp.integrated_loudness(mix, sr)
    gain = settings.target_lufs - L
    out = mix
    lim_max = lim_mean = 0.0
    for _ in range(4):
        out, lim_max, lim_mean = dsp.limiter(mix * float(dsp.db2lin(gain)), sr, settings.ceiling_db)
        L2 = dsp.integrated_loudness(out, sr)
        err = settings.target_lufs - L2
        if abs(err) < 0.15:
            break
        gain += err
    final_lufs = dsp.integrated_loudness(out, sr)

    # taglio del silenzio finale + breve dissolvenza
    env = np.max(np.abs(out), axis=1)
    nz = np.nonzero(env > dsp.db2lin(-70))[0]
    end = min(out.shape[0], (nz[-1] + int(0.3 * sr)) if nz.size else out.shape[0])
    out = out[:end]
    fade = min(int(0.05 * sr), out.shape[0])
    out[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]

    progress(0.97, "Salvo il file")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write_wav(out_path, out, sr, settings.bits)
    master = dict(lufs=final_lufs, peak_db=float(dsp.lin2db(np.abs(out).max())),
                  lim_max=lim_max, lim_mean=lim_mean, glue=glue, gain=gain,
                  duration=out.shape[0] / sr, sr=sr)
    report = make_report(results, master, settings, tracks)
    rep_path = os.path.splitext(out_path)[0] + "_report.txt"
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write(report)
    progress(1.0, "Fatto")
    return dict(path=out_path, report_path=rep_path, report=report, master=master)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _pan_txt(p):
    if abs(p) < 0.03:
        return "centro"
    return f"{abs(p) * 100:.0f}% {'sinistra' if p < 0 else 'destra'} (Logic: {p * 64:+.0f})"


def _hz(f):
    return f"{f / 1000:.1f} kHz" if f >= 1000 else f"{f:.0f} Hz"


def make_report(results, master, settings, tracks) -> str:
    L = []
    L.append("BLACKMIX - REPORT DEL MIX")
    L.append("=" * 60)
    L.append(f"Genere: {settings.genre}   Loudness obiettivo: {settings.target_lufs:.0f} LUFS")
    L.append(f"Risultato: {master['lufs']:.1f} LUFS integrati, picco {master['peak_db']:.1f} dBFS, "
             f"durata {int(master['duration'] // 60)}:{master['duration'] % 60:04.1f}")
    if master["lim_max"] > 6:
        L.append(f"ATTENZIONE: il limiter lavora parecchio (fino a {master['lim_max']:.1f} dB). "
                 "Se suona schiacciato prova un loudness obiettivo piu' basso.")
    L.append("")
    L.append("Volumi e pan sono quelli finali. 'Fader' e' la correzione applicata dopo EQ e compressione.")
    L.append("Le impostazioni sono pensate anche per essere rifatte in Logic (Channel EQ, Compressor).")
    L.append("")
    for r in results:
        t, p = r["track"], r["plan"]
        role = ROLES[p["role"]]
        L.append("-" * 60)
        L.append(f"{t.name}   [{role.label}]")
        if t.offset:
            L.append(f"  Inizio: {t.offset:.2f} s dall'inizio del mix")
        L.append(f"  Fader: {r['fader']:+.1f} dB   Pan: {_pan_txt(p['pan'])}")
        if p["invert"]:
            L.append("  Polarita' invertita (era in controfase con un'altra traccia dello stesso tipo)")
        eqs = [f"passa-alto {_hz(p['hpf'])}"] if p["hpf"] else []
        for b in p["eq"]:
            kind = {"peak": "campana", "lowshelf": "shelf basso", "highshelf": "shelf alto"}[b["type"]]
            eqs.append(f"{kind} {_hz(b['f'])} {b['g']:+.1f} dB" + (f" Q {b['q']:.1f}" if b["type"] == "peak" else ""))
        L.append("  EQ: " + ", ".join(eqs) if eqs else "  EQ: nessuna")
        for b in p["unmask"]:
            L.append(f"  EQ spazio: {_hz(b['f'])} {b['g']:+.1f} dB Q {b['q']:.1f}  (per far spazio a: {b['why']})")
        c = r["comp"]
        if c:
            L.append(f"  Compressore: soglia {c['thr']:.1f} dBFS, ratio {c['ratio']:.1f}:1, attacco "
                     f"{c['att']:.0f} ms, rilascio {c['rel']:.0f} ms -> riduzione media {c['mean_gr']:.1f} dB, "
                     f"max {c['max_gr']:.1f} dB")
        if p["deess"]:
            L.append(f"  De-esser 5-10 kHz: riduzione massima {r['deess']:.1f} dB")
        if p["send"] is not None:
            L.append(f"  Mandata riverbero: {p['send']:.1f} dB")
    L.append("-" * 60)
    g = master["glue"]
    L.append("MASTER")
    if g:
        L.append(f"  Compressore bus: ratio 1.8:1, attacco 30 ms, rilascio 250 ms, riduzione media "
                 f"{g['mean_gr']:.1f} dB")
    L.append(f"  Limiter: tetto {settings.ceiling_db:.1f} dBFS, riduzione media {master['lim_mean']:.1f} dB, "
             f"max {master['lim_max']:.1f} dB")
    skipped = [t for t in tracks if not any(r["track"] is t for r in results)]
    if skipped:
        L.append("")
        L.append("Tracce escluse:")
        for t in skipped:
            why = ", ".join(t.notes) if t.notes else "deselezionata"
            L.append(f"  - {t.name} ({why})")
    L.append("")
    L.append("Nota: i file sono presi dalle registrazioni originali del progetto. Tagli, dissolvenze,")
    L.append("comping e automazioni fatti in Logic non vengono letti.")
    return "\n".join(L) + "\n"
