"""Verifica che ogni eseguibile e libreria dentro l'app giri sul macOS minimo voluto.

Uso: python3 check_minos.py BlackMix.app 10.15
Legge con `otool -l` la versione minima dichiarata da ogni file Mach-O
(LC_BUILD_VERSION "minos" o LC_VERSION_MIN_MACOSX "version") e fallisce se
qualcuno chiede un macOS piu' recente.
"""
import os
import subprocess
import sys

MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def ver(s):
    return tuple(int(p) for p in s.split(".")[:3]) + (0,) * (3 - len(s.split(".")[:3]))


def min_versions(path):
    out = subprocess.run(["otool", "-arch", "all", "-l", path], capture_output=True, text=True).stdout
    found = []
    mode = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("cmd "):
            mode = line.split()[1]
        elif mode == "LC_BUILD_VERSION" and line.startswith("minos "):
            found.append(line.split()[1])
        elif mode == "LC_VERSION_MIN_MACOSX" and line.startswith("version "):
            found.append(line.split()[1])
    return found


def main():
    app, target = sys.argv[1], sys.argv[2]
    tv = ver(target)
    checked, bad, highest = 0, [], "0"
    for root, _dirs, files in os.walk(app):
        for fn in files:
            p = os.path.join(root, fn)
            if os.path.islink(p):
                continue
            try:
                with open(p, "rb") as f:
                    if f.read(4) not in MAGICS:
                        continue
            except OSError:
                continue
            vs = min_versions(p)
            if not vs:
                continue
            checked += 1
            for v in vs:
                if ver(v) > ver(highest):
                    highest = v
                if ver(v) > tv:
                    bad.append((v, os.path.relpath(p, app)))
    print(f"File binari controllati: {checked}; versione minima piu' alta richiesta: macOS {highest}")
    if checked == 0:
        print("Nessun binario trovato: qualcosa non va")
        sys.exit(1)
    if bad:
        print(f"Questi file richiedono un macOS piu' recente di {target}:")
        for v, p in sorted(set(bad), reverse=True)[:40]:
            print(f"  macOS {v}: {p}")
        sys.exit(1)
    print(f"Tutto compatibile con macOS {target}")


if __name__ == "__main__":
    main()
