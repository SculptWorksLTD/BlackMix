"""Elaborazioni audio: filtri, loudness, compressore, de-esser, riverbero, limiter.

Tutto in numpy/scipy. I segnali sono array float32 di forma (campioni, canali).
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

EPS = 1e-12


def db2lin(db):
    return 10.0 ** (np.asarray(db, dtype=np.float64) / 20.0)


def lin2db(x):
    return 20.0 * np.log10(np.maximum(np.asarray(x, dtype=np.float64), EPS))


# --------------------------------------------------------------------------
# Biquad (RBJ cookbook) -> riga SOS [b0 b1 b2 1 a1 a2]
# --------------------------------------------------------------------------

def _sos(b0, b1, b2, a0, a1, a2):
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def peaking(f, gain_db, q, sr):
    A = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * min(f, 0.45 * sr) / sr
    al = math.sin(w) / (2 * q)
    c = math.cos(w)
    return _sos(1 + al * A, -2 * c, 1 - al * A, 1 + al / A, -2 * c, 1 - al / A)


def low_shelf(f, gain_db, sr, q=0.707):
    A = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * min(f, 0.45 * sr) / sr
    al = math.sin(w) / (2 * q)
    c = math.cos(w)
    s = 2 * math.sqrt(A) * al
    return _sos(A * ((A + 1) - (A - 1) * c + s), 2 * A * ((A - 1) - (A + 1) * c),
                A * ((A + 1) - (A - 1) * c - s), (A + 1) + (A - 1) * c + s,
                -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - s)


def high_shelf(f, gain_db, sr, q=0.707):
    A = 10 ** (gain_db / 40.0)
    w = 2 * math.pi * min(f, 0.45 * sr) / sr
    al = math.sin(w) / (2 * q)
    c = math.cos(w)
    s = 2 * math.sqrt(A) * al
    return _sos(A * ((A + 1) + (A - 1) * c + s), -2 * A * ((A - 1) + (A + 1) * c),
                A * ((A + 1) + (A - 1) * c - s), (A + 1) - (A - 1) * c + s,
                2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - s)


def highpass(f, sr, order=2):
    return signal.butter(order, min(f, 0.45 * sr), "highpass", fs=sr, output="sos")


def lowpass(f, sr, order=2):
    return signal.butter(order, min(f, 0.45 * sr), "lowpass", fs=sr, output="sos")


def bandpass(f1, f2, sr, order=2):
    return signal.butter(order, [f1, min(f2, 0.45 * sr)], "bandpass", fs=sr, output="sos")


def apply_sos(x, sos):
    return signal.sosfilt(sos, x, axis=0).astype(np.float32)


def eq_chain(x, sr, hpf_hz, bands):
    """bands: lista di dict {type: 'peak'|'lowshelf'|'highshelf', f, g, q}"""
    rows = []
    if hpf_hz:
        rows.append(highpass(hpf_hz, sr, order=4))
    for b in bands:
        if abs(b["g"]) < 0.05:
            continue
        if b["type"] == "peak":
            rows.append(peaking(b["f"], b["g"], b.get("q", 1.0), sr))
        elif b["type"] == "lowshelf":
            rows.append(low_shelf(b["f"], b["g"], sr))
        elif b["type"] == "highshelf":
            rows.append(high_shelf(b["f"], b["g"], sr))
    if not rows:
        return x
    return apply_sos(x, np.vstack(rows))


# --------------------------------------------------------------------------
# Loudness ITU-R BS.1770 / EBU R128
# --------------------------------------------------------------------------

def _k_weighting_sos(sr):
    # stadio 1: shelf alto +4 dB a 1500 Hz ; stadio 2: passa-alto RLB a 38 Hz
    s1 = high_shelf(1500.0, 4.0, sr, q=1 / math.sqrt(2))
    w = 2 * math.pi * 38.0 / sr
    c = math.cos(w)
    al = math.sin(w) / (2 * 0.5)
    s2 = _sos((1 + c) / 2, -(1 + c), (1 + c) / 2, 1 + al, -2 * c, 1 - al)
    return np.vstack([s1, s2])


def _block_powers(x, sr):
    """Potenze medie K-pesate su blocchi da 400 ms con passo 100 ms (somma canali)."""
    if x.ndim == 1:
        x = x[:, None]
    y = signal.sosfilt(_k_weighting_sos(sr), x.astype(np.float64), axis=0)
    hop = int(round(0.1 * sr))
    n = y.shape[0] // hop
    if n < 4:
        return np.array([])
    seg = (y[: n * hop] ** 2).reshape(n, hop, -1).mean(axis=1).sum(axis=1)  # (n,)
    cs = np.concatenate([[0.0], np.cumsum(seg)])
    return (cs[4:] - cs[:-4]) / 4.0


def integrated_loudness(x, sr):
    """LUFS integrati (con gating). Restituisce -inf se il segnale e' silenzioso."""
    p = _block_powers(x, sr)
    if p.size == 0:
        return float("-inf")
    l = -0.691 + 10 * np.log10(p + EPS)
    g = p[l > -70.0]
    if g.size == 0:
        return float("-inf")
    rel = -0.691 + 10 * np.log10(g.mean()) - 10.0
    g2 = p[(l > -70.0) & (l > rel)]
    if g2.size == 0:
        return float("-inf")
    return float(-0.691 + 10 * np.log10(g2.mean()))


def short_term_levels(x, sr, win=3.0):
    p = _block_powers(x, sr)
    if p.size == 0:
        return np.array([])
    k = max(1, int(win / 0.1) - 3)
    p = uniform_filter1d(p, k)
    return -0.691 + 10 * np.log10(p + EPS)


# --------------------------------------------------------------------------
# Dinamica
# --------------------------------------------------------------------------

def _block_levels_db(det, block):
    n = det.shape[0] // block
    if n == 0:
        return np.array([]), block
    ms = (det[: n * block].astype(np.float64) ** 2).reshape(n, block).mean(axis=1)
    return 10 * np.log10(ms + EPS), block


def _smooth_gain(target_db, block_s, attack_ms, release_ms):
    """Inviluppo attacco/rilascio sulla riduzione di guadagno (valori <= 0 dB)."""
    a_att = math.exp(-block_s / max(attack_ms / 1000.0, 1e-4))
    a_rel = math.exp(-block_s / max(release_ms / 1000.0, 1e-4))
    out = np.empty_like(target_db)
    g = 0.0
    t = target_db.tolist()
    for i, v in enumerate(t):
        if v < g:
            g = a_att * g + (1 - a_att) * v
        else:
            g = a_rel * g + (1 - a_rel) * v
        out[i] = g
    return out


def _gain_to_samples(gr_db, block, nsamp):
    centers = (np.arange(gr_db.size) + 0.5) * block
    lin = db2lin(gr_db)
    return np.interp(np.arange(nsamp), centers, lin).astype(np.float32)


def detector_levels(x, sr, block_ms=2.0):
    det = np.max(np.abs(x), axis=1) if x.ndim > 1 else np.abs(x)
    block = max(8, int(sr * block_ms / 1000.0))
    lev, block = _block_levels_db(det, block)
    return lev, block


def compressor(x, sr, threshold_db, ratio, attack_ms, release_ms, knee_db=6.0):
    """Compressore feed-forward RMS con ginocchio morbido, canali collegati.
    Restituisce (uscita, riduzione media in dB sui tratti attivi, riduzione massima)."""
    if ratio <= 1.0:
        return x, 0.0, 0.0
    block = max(16, int(sr * 0.001))
    det = np.max(np.abs(x), axis=1)
    lev, block = _block_levels_db(det, block)
    if lev.size == 0:
        return x, 0.0, 0.0
    over = lev - threshold_db
    slope = 1.0 / ratio - 1.0
    gr = np.where(over <= -knee_db / 2, 0.0,
                  np.where(over >= knee_db / 2, slope * over,
                           slope * (over + knee_db / 2) ** 2 / (2 * knee_db)))
    gr_s = _smooth_gain(gr, block / sr, attack_ms, release_ms)
    g = _gain_to_samples(gr_s, block, x.shape[0])
    active = lev > (lev.max() - 40)
    mean_gr = float(-gr_s[active].mean()) if active.any() else 0.0
    return (x * g[:, None]).astype(np.float32), mean_gr, float(-gr_s.min())


def deesser(x, sr, f_lo=5000.0, f_hi=10000.0, ratio_db=-6.0, max_red_db=8.0):
    """Riduce la banda sibilante quando domina sul segnale intero.
    Restituisce (uscita, riduzione massima in dB)."""
    if sr < 2.2 * f_hi:
        f_hi = 0.45 * sr
    band = apply_sos(x, bandpass(f_lo, f_hi, sr, order=2))
    block = max(16, int(sr * 0.002))
    lb, _ = _block_levels_db(np.max(np.abs(band), axis=1), block)
    lf, _ = _block_levels_db(np.max(np.abs(x), axis=1), block)
    if lb.size == 0:
        return x, 0.0
    excess = (lb - lf) - ratio_db
    red = -np.clip(excess, 0.0, max_red_db)
    red[lf < lf.max() - 45] = 0.0
    red_s = _smooth_gain(red, block / sr, 1.0, 60.0)
    g = _gain_to_samples(red_s, block, x.shape[0])
    y = x + band * (g[:, None] - 1.0)
    return y.astype(np.float32), float(-red_s.min())


# --------------------------------------------------------------------------
# Panorama
# --------------------------------------------------------------------------

def pan_to_stereo(x, pan):
    """pan in [-1, 1]. Mono: legge a potenza costante. Stereo: bilanciamento."""
    pan = float(np.clip(pan, -1, 1))
    if x.shape[1] == 1:
        th = (pan + 1) * math.pi / 4
        return np.hstack([x * math.cos(th), x * math.sin(th)]).astype(np.float32)
    if x.shape[1] > 2:
        x = np.stack([x[:, 0], x[:, 1]], axis=1)
    gl = math.sqrt(min(1.0, 1.0 - pan)) if pan > 0 else 1.0
    gr = math.sqrt(min(1.0, 1.0 + pan)) if pan < 0 else 1.0
    return np.stack([x[:, 0] * gl, x[:, 1] * gr], axis=1).astype(np.float32)


# --------------------------------------------------------------------------
# Riverbero (convoluzione con risposta all'impulso sintetica)
# --------------------------------------------------------------------------

def make_reverb_ir(sr, rt60=1.8, predelay_ms=25.0, seed=7):
    rng = np.random.default_rng(seed)
    n = int(sr * min(rt60 * 1.2, 4.0))
    t = np.arange(n) / sr
    env = np.exp(-6.91 * t / rt60)
    # attacco morbido della coda
    env *= np.clip(t / 0.015, 0, 1)
    ir = rng.standard_normal((n, 2)) * env[:, None]
    # prime riflessioni
    for k, (dt, a) in enumerate([(0.007, 0.5), (0.011, 0.4), (0.017, 0.35), (0.023, 0.3), (0.031, 0.25)]):
        i = int(dt * sr)
        if i < n:
            ir[i, k % 2] += a * 6
    # smorzamento degli acuti piu' rapido: mix di versione filtrata che decade prima
    lp = signal.sosfilt(lowpass(3500.0, sr, 2), ir, axis=0)
    fade = np.exp(-6.91 * t / (rt60 * 0.35))[:, None]
    ir = lp + (ir - lp) * fade
    ir = signal.sosfilt(np.vstack([highpass(200.0, sr, 2), lowpass(9000.0, sr, 2)]), ir, axis=0)
    pre = np.zeros((int(sr * predelay_ms / 1000.0), 2))
    ir = np.vstack([pre, ir])
    ir /= math.sqrt((ir ** 2).sum() / 2) + EPS
    return ir.astype(np.float32)


def convolve_reverb(send_mono, ir):
    out = np.empty((send_mono.shape[0] + ir.shape[0] - 1, 2), dtype=np.float32)
    for c in range(2):
        out[:, c] = signal.oaconvolve(send_mono, ir[:, c])
    return out


# --------------------------------------------------------------------------
# Limiter con lookahead
# --------------------------------------------------------------------------

def limiter(x, sr, ceiling_db=-1.0, lookahead_ms=5.0, release_ms=120.0):
    """Restituisce (uscita, riduzione massima dB, riduzione media dB sui tratti attivi)."""
    ceil = float(db2lin(ceiling_db))
    peak = true_peak_env(x)
    req = np.minimum(1.0, ceil / np.maximum(peak, EPS))
    B = 16
    n = len(req) // B + 1
    padded = np.ones(n * B)
    padded[: len(req)] = req
    blk = padded.reshape(n, B).min(axis=1)
    # attacco istantaneo, rilascio esponenziale (sui blocchi)
    a = math.exp(-(B / sr) / (release_ms / 1000.0))
    gdb = lin2db(blk)
    out = np.empty_like(gdb)
    g = 0.0
    for i, v in enumerate(gdb.tolist()):
        g = v if v < g else a * g + (1 - a) * v
        out[i] = g
    gs = np.repeat(db2lin(out), B)[: len(req)]
    gs = np.minimum(gs, req)
    L = max(1, int(sr * lookahead_ms / 1000.0))
    # minimo sul futuro [n, n+L-1], poi media sul passato [n-L+1, n]:
    # cosi' la riduzione scende gradualmente e arriva in tempo sul picco
    fut = _future_min(gs, L)
    sm = _past_mean(fut, L)
    y = x * sm[:, None].astype(np.float32)
    y = np.clip(y, -ceil, ceil)
    act = peak > ceil * 0.01
    max_gr = float(-lin2db(sm.min()))
    mean_gr = float(-lin2db(sm[act]).mean()) if act.any() else 0.0
    return y.astype(np.float32), max_gr, mean_gr


def true_peak_env(x, os_factor=4, chunk=1 << 19):
    """Picco per campione stimato con sovracampionamento x4 (picchi tra i campioni)."""
    n = x.shape[0]
    out = np.empty(n)
    pad = 64
    for s in range(0, n, chunk):
        a = max(0, s - pad)
        b = min(n, s + chunk + pad)
        up = signal.resample_poly(x[a:b].astype(np.float64), os_factor, 1, axis=0)
        pk = np.max(np.abs(up), axis=1)
        pk = pk[: (b - a) * os_factor].reshape(b - a, os_factor).max(axis=1)
        # considera anche l'intervallo verso il campione successivo
        pk = np.maximum(pk, np.concatenate([pk[1:], pk[-1:]]))
        out[s: min(n, s + chunk)] = pk[s - a: s - a + min(chunk, n - s)]
    return np.maximum(out, np.max(np.abs(x), axis=1))


def _future_min(g, L):
    """m[n] = min(g[n .. n+L-1])"""
    if L <= 1 or g.size == 0:
        return g.copy()
    padded = np.concatenate([g, np.full(L, g[-1])])
    m = minimum_filter1d(padded, L, mode="nearest")
    # la finestra di minimum_filter1d sull'indice i copre [i - L//2, i - L//2 + L - 1]
    shift = L // 2
    return m[shift: shift + g.size]


def _past_mean(a, L):
    """media di a[n-L+1 .. n]; prima dell'inizio si usa a[0]."""
    if L <= 1:
        return a.copy()
    padded = np.concatenate([np.full(L - 1, a[0]), a])
    cs = np.concatenate([[0.0], np.cumsum(padded)])
    return (cs[L:] - cs[:-L]) / L
