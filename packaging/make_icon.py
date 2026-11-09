"""Genera packaging/BlackMix.icns (e un'anteprima PNG) senza librerie grafiche."""
import os, struct, zlib
import numpy as np

S = 1024
SS = 2  # sovracampionamento per bordi morbidi
N = S * SS
y, x = np.mgrid[0:N, 0:N].astype(np.float32) / SS


def rounded_rect(x0, y0, x1, y1, r):
    cx = np.clip(x, x0 + r, x1 - r)
    cy = np.clip(y, y0 + r, y1 - r)
    return ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)


img = np.zeros((N, N, 4), np.float32)


def paint(mask, rgb, a=1.0):
    m = mask.astype(np.float32) * a
    for c in range(3):
        img[..., c] = img[..., c] * (1 - m) + rgb[c] * m
    img[..., 3] = np.maximum(img[..., 3], m)


# fondo: quadrato arrotondato nero con leggero gradiente
bg = rounded_rect(100, 100, 924, 924, 185)
t = (y - 100) / 824
for c, (a0, a1) in enumerate([(0.13, 0.03), (0.13, 0.03), (0.15, 0.04)]):
    img[..., c] = np.where(bg, a0 + (a1 - a0) * t, 0)
img[..., 3] = bg.astype(np.float32)
# bordo sottile
paint(rounded_rect(100, 100, 924, 924, 185) & ~rounded_rect(106, 106, 918, 918, 179), (0.35, 0.35, 0.38))

# quattro fader
xs = [300, 440, 584, 724]
knobs = [560, 380, 470, 640]
accent = np.array([1.0, 0.36, 0.12])  # arancio
for i, (cx, ky) in enumerate(zip(xs, knobs)):
    paint(rounded_rect(cx - 9, 250, cx + 9, 780, 9), (0.30, 0.30, 0.33))          # binario
    paint(rounded_rect(cx - 9, ky, cx + 9, 780, 9), accent * (0.75 + 0.08 * i))  # livello
    paint(rounded_rect(cx - 52, ky - 26, cx + 52, ky + 26, 14), (0.93, 0.93, 0.95))  # cursore
    paint(rounded_rect(cx - 40, ky - 3, cx + 40, ky + 3, 3), (0.2, 0.2, 0.22))

# riduzione
small = img.reshape(S, SS, S, SS, 4).mean(axis=(1, 3))


def png_bytes(a):
    h, w = a.shape[:2]
    rgba = (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
    raw = b"".join(b"\x00" + rgba[r].tobytes() for r in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def resize(a, n):
    k = a.shape[0] // n
    return a.reshape(n, k, n, k, 4).mean(axis=(1, 3))


here = os.path.dirname(os.path.abspath(__file__))
entries = [(b"ic10", 1024), (b"ic09", 512), (b"ic08", 256), (b"ic07", 128)]
body = b""
for typ, n in entries:
    d = png_bytes(resize(small, n))
    body += typ + struct.pack(">I", len(d) + 8) + d
with open(os.path.join(here, "BlackMix.icns"), "wb") as f:
    f.write(b"icns" + struct.pack(">I", len(body) + 8) + body)
with open(os.path.join(here, "icon_preview.png"), "wb") as f:
    f.write(png_bytes(resize(small, 256)))  # solo anteprima, non va nel repo
print("ok")
