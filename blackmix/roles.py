"""Ruoli degli strumenti: riconoscimento dal nome/suono e impostazioni di mix di base."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Role:
    key: str
    label: str
    level: float          # loudness rispetto alla voce (LU)
    priority: int         # chi "vince" quando due tracce si coprono
    hpf: float | None     # passa-alto (Hz)
    eq: list = field(default_factory=list)
    comp: tuple = (2.0, 10.0, 120.0, 3.0)   # ratio, attacco ms, rilascio ms, riduzione desiderata dB
    deess: bool = False
    reverb: float | None = None             # mandata al riverbero (dB), None = nessuna
    pan: str | float = 0.0                   # numero, oppure 'spread' / 'spread_wide' / 'side'
    key_bands: list = field(default_factory=list)  # zone di frequenza da proteggere [(f1, f2)]


def _pk(f, g, q=1.0):
    return {"type": "peak", "f": f, "g": g, "q": q}


def _ls(f, g):
    return {"type": "lowshelf", "f": f, "g": g}


def _hs(f, g):
    return {"type": "highshelf", "f": f, "g": g}


ROLES: dict[str, Role] = {r.key: r for r in [
    Role("voce", "Voce principale", 0.0, 10, 90, [_pk(300, -2.0, 1.2), _pk(3200, 2.0, 1.0), _hs(10000, 2.0)],
         (3.5, 5.0, 90.0, 6.0), True, -14.0, 0.0, [(250, 450), (1000, 5000)]),
    Role("cori", "Cori / seconde voci", -7.0, 6, 150, [_pk(350, -3.0, 1.0), _hs(9000, 2.0)],
         (4.0, 5.0, 100.0, 6.0), True, -10.0, "spread", [(1500, 4000)]),
    Role("cassa", "Cassa", -3.0, 9, 30, [_pk(60, 2.5, 1.2), _pk(380, -4.0, 1.4), _pk(3500, 2.0, 1.2)],
         (4.0, 15.0, 80.0, 3.0), False, None, 0.0, [(45, 110), (2500, 5000)]),
    Role("rullante", "Rullante", -3.0, 8, 80, [_pk(200, 1.5, 1.2), _pk(900, -2.0, 1.5), _hs(6000, 1.5)],
         (4.0, 10.0, 120.0, 4.0), False, -16.0, 0.0, [(160, 280)]),
    Role("tom", "Tom", -8.0, 4, 60, [_pk(400, -3.0, 1.4), _pk(4000, 2.0, 1.2)],
         (3.0, 10.0, 150.0, 3.0), False, -18.0, "spread", []),
    Role("hihat", "Hi-hat", -12.0, 3, 300, [_pk(800, -2.0, 1.0), _hs(10000, 1.5)],
         (2.0, 5.0, 80.0, 1.0), False, None, 0.35, []),
    Role("overhead", "Overhead / piatti", -8.0, 3, 150, [_pk(500, -2.0, 1.0), _hs(10000, 1.5)],
         (2.0, 20.0, 200.0, 1.5), False, None, "spread_wide", []),
    Role("room", "Room / ambiente", -12.0, 1, 80, [_pk(400, -2.0, 1.0)],
         (4.0, 10.0, 150.0, 5.0), False, None, "spread_wide", []),
    Role("basso", "Basso", -3.0, 8, 35, [_pk(250, -2.0, 1.2), _pk(800, 1.5, 1.0)],
         (4.0, 20.0, 150.0, 5.0), False, None, 0.0, [(50, 200)]),
    Role("chitarra_el", "Chitarra elettrica", -5.0, 5, 100, [_pk(400, -1.5, 1.0), _pk(2500, 1.0, 1.0)],
         (2.5, 15.0, 150.0, 2.5), False, -18.0, "side", [(1500, 3000)]),
    Role("chitarra_ac", "Chitarra acustica", -6.0, 5, 90, [_pk(250, -2.5, 1.0), _hs(9000, 2.0)],
         (3.0, 10.0, 120.0, 3.5), False, -16.0, "side", []),
    Role("piano", "Piano", -5.0, 5, 60, [_pk(300, -1.5, 1.0), _hs(8000, 1.0)],
         (2.0, 15.0, 150.0, 2.0), False, -15.0, 0.15, []),
    Role("tastiere", "Synth / tastiere", -7.0, 4, 80, [_pk(400, -1.5, 1.0)],
         (2.0, 15.0, 150.0, 2.0), False, -17.0, "spread", []),
    Role("pad", "Pad / atmosfere", -10.0, 2, 120, [_pk(500, -2.0, 0.8)],
         (1.5, 30.0, 300.0, 1.0), False, -14.0, 0.0, []),
    Role("archi", "Archi", -8.0, 4, 100, [_pk(350, -1.5, 1.0), _hs(9000, 1.0)],
         (1.8, 30.0, 250.0, 1.5), False, -12.0, "spread", []),
    Role("fiati", "Fiati", -6.0, 5, 120, [_pk(400, -1.5, 1.0)],
         (2.5, 10.0, 150.0, 3.0), False, -15.0, "spread", []),
    Role("percussioni", "Percussioni", -11.0, 3, 120, [],
         (2.0, 10.0, 120.0, 2.0), False, -17.0, "spread", []),
    Role("altro", "Altro", -8.0, 3, 60, [],
         (2.0, 15.0, 150.0, 2.0), False, -18.0, 0.0, []),
]}

ROLE_ORDER = list(ROLES.keys())


# Preset di genere: aggiustamenti ai livelli (LU), alla compressione e al riverbero
GENRES = {
    "Pop": dict(levels={"voce": 1.0}, comp=1.0, reverb=0.0),
    "Rock": dict(levels={"voce": -0.5, "chitarra_el": 1.5, "rullante": 1.0, "cassa": 0.5},
                 comp=1.15, reverb=-2.0),
    "Acustico / cantautore": dict(levels={"voce": 1.0, "chitarra_ac": 1.5, "piano": 1.0},
                                  comp=0.7, reverb=2.0),
    "Elettronico": dict(levels={"cassa": 1.5, "basso": 1.0, "pad": 2.0, "tastiere": 1.5},
                        comp=1.2, reverb=0.0),
    "Jazz / live": dict(levels={"overhead": 3.0, "room": 3.0, "voce": -0.5},
                        comp=0.5, reverb=1.0),
}


_PATTERNS = [
    ("cori", r"\bcor[oi]\b|backing|bvox|\bbv\b|bkg|harmon|armoni|choir|seconda voce|doppia"),
    ("cassa", r"kick|cassa|\bbd\b|bass ?drum|\bkik"),
    ("rullante", r"snare|rullant|\brull|\bsn\b|\bsnr"),
    ("hihat", r"hi.?hat|\bhh\b|charleston|\bhat"),
    ("tom", r"\btom|floor|timpano"),
    ("overhead", r"overhead|\boh\b|\bovh|piatti|cymbal|crash|\bride"),
    ("room", r"\broom|ambien|\bamb\b|stanza"),
    ("voce", r"\bvox|voce|vocal|\bvoc\b|canto|cantant|lead ?v|singer|\bvx"),
    ("chitarra_ac", r"acous|acust|ac ?gtr|nylon|classica|\bacg"),
    ("basso", r"bass|basso|\bbs\b|contrabb"),
    ("chitarra_el", r"gtr|guit|chit|\bgt\b|elett|riff"),
    ("piano", r"piano|\bpno|rhodes|wurli|e ?piano|\bep\b"),
    ("pad", r"\bpad|atmos|drone"),
    ("archi", r"string|archi|violin|viola|cello|violon"),
    ("fiati", r"horn|fiati|\bsax|trombon|trumpet|tromba|brass|flaut|flute|clarin"),
    ("tastiere", r"synth|keys|tastier|organ|hammond|\bkey|moog|\barp"),
    ("percussioni", r"perc|shaker|tambour|tamburell|conga|bongo|cajon|clap|cowbell"),
]


def guess_from_name(name: str) -> str | None:
    s = name.lower()
    s = re.sub(r"[_\-\.]+", " ", s)
    s = re.sub(r"#\d+", " ", s)
    for role, pat in _PATTERNS:
        if re.search(pat, s):
            return role
    return None


def guess_from_features(feat: dict) -> str:
    """Ipotesi grossolana dal contenuto, usata solo se il nome non dice nulla."""
    c = feat.get("centroid", 1000)
    low = feat.get("low_ratio", 0)
    crest = feat.get("crest_db", 10)
    stereo = feat.get("channels", 1) > 1
    if low > 0.6 and crest > 16:
        return "cassa"
    if low > 0.45 and c < 400:
        return "basso"
    if c > 5000 and crest > 14:
        return "hihat"
    if stereo and c > 2500:
        return "overhead"
    if 900 < c < 3500 and crest > 18:
        return "rullante"
    if stereo:
        return "tastiere"
    return "altro"


def side_hint(name: str) -> float | None:
    """-1 se il nome indica sinistra, +1 destra."""
    s = " " + re.sub(r"[_\-\.#]+", " ", name.lower()) + " "
    if re.search(r" (l|left|sx|sinistr\w*) ", s):
        return -1.0
    if re.search(r" (r|right|dx|destr\w*) ", s):
        return 1.0
    return None
