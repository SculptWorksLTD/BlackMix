#!/bin/bash
# Costruisce BlackMix.app (con Python, numpy e scipy dentro) e il pacchetto .pkg.
# Va eseguito su macOS. Uso: packaging/build_pkg.sh 0.1.0
set -euo pipefail

VERSION="${1:?Uso: build_pkg.sh VERSIONE}"
PY_VER="${PY_VER:-3.12}"
ARCH="$(uname -m)"                 # arm64 / x86_64
# macOS minimo: Intel fino a Catalina 10.15, Apple Silicon da Big Sur 11
if [ "$ARCH" = "x86_64" ]; then
  MIN_MACOS="${MIN_MACOS:-10.15}"; PBS_ARCH="x86_64"
else
  MIN_MACOS="${MIN_MACOS:-11.0}"; PBS_ARCH="aarch64"
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
APP="$BUILD/root/BlackMix.app"
ID="com.sculptworks.blackmix"

command -v uv >/dev/null || { echo "Serve uv: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

rm -rf "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$BUILD/scripts"

echo "==> Python $PY_VER autonomo ($ARCH, macOS $MIN_MACOS o successivo)"
uv python install "cpython-$PY_VER-macos-$PBS_ARCH-none"
PYHOME="$(ls -d "$(uv python dir)"/cpython-"$PY_VER"*-macos-"$PBS_ARCH"-* | grep -v freethreaded | head -1)"
[ -d "$PYHOME" ] || { echo "Python gestito da uv non trovato"; exit 1; }
# uv crea anche collegamenti simbolici "cpython-3.12-..." -> cartella vera: va risolto
PYHOME="$(cd "$PYHOME" && pwd -P)"
cp -R "$PYHOME/" "$APP/Contents/Resources/python"
[ -L "$APP/Contents/Resources/python" ] && { echo "Python copiato come collegamento"; exit 1; }
# nessun collegamento deve puntare fuori dall'app
if find "$APP/Contents/Resources/python" -type l -exec readlink {} \; | grep -q '^/'; then
  echo "Collegamenti assoluti dentro Python:"; find "$APP/Contents/Resources/python" -type l -exec sh -c 'case "$(readlink "$1")" in /*) echo "$1 -> $(readlink "$1")";; esac' _ {} \;
  exit 1
fi
PY="$APP/Contents/Resources/python/bin/python3"
rm -f "$APP"/Contents/Resources/python/lib/python3*/EXTERNALLY-MANAGED

echo "==> Librerie"
# solo pacchetti precompilati compatibili con il macOS minimo scelto:
# uv scarta le versioni di numpy/scipy che richiedono un macOS piu' recente
MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS" uv pip install --python "$PY" \
  --python-platform "$PBS_ARCH-apple-darwin" --only-binary :all: \
  -r "$ROOT/requirements.txt" certifi
"$PY" -c "import numpy, scipy; print('numpy', numpy.__version__, '- scipy', scipy.__version__)"
# un po' di dimagrimento
rm -rf "$APP"/Contents/Resources/python/lib/python3*/test \
       "$APP"/Contents/Resources/python/lib/python3*/idlelib \
       "$APP"/Contents/Resources/python/lib/python3*/ensurepip
find "$APP/Contents/Resources/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$APP/Contents/Resources/python" -path "*/site-packages/*/tests" -type d -prune -exec rm -rf {} +

echo "==> Codice di BlackMix"
cp -R "$ROOT/blackmix" "$APP/Contents/Resources/app/"
find "$APP/Contents/Resources/app" -name "__pycache__" -type d -prune -exec rm -rf {} +
sed -i '' "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$APP/Contents/Resources/app/blackmix/__init__.py"

cat > "$APP/Contents/MacOS/BlackMix" <<'EOF'
#!/bin/bash
C="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$C/Resources/app"
export PYTHONNOUSERSITE=1
exec "$C/Resources/python/bin/python3" -m blackmix "$@"
EOF
chmod +x "$APP/Contents/MacOS/BlackMix"

sed -e "s/@VERSION@/$VERSION/g" -e "s/@ID@/$ID/g" -e "s/@MINOS@/$MIN_MACOS/g" "$ROOT/packaging/Info.plist" > "$APP/Contents/Info.plist"
if [ -f "$ROOT/packaging/BlackMix.icns" ]; then
  cp "$ROOT/packaging/BlackMix.icns" "$APP/Contents/Resources/"
fi

echo "==> Prova del pacchetto"
du -sh "$APP"
SIZE_MB=$(du -sm "$APP" | cut -f1)
[ "$SIZE_MB" -gt 40 ] || { echo "App troppo piccola ($SIZE_MB MB): manca qualcosa"; exit 1; }
# prova con l'app spostata altrove, per essere sicuri che non dipenda dal percorso di build
MOVED="$(mktemp -d)/BlackMix.app"
cp -R "$APP" "$MOVED"
mv "$(uv python dir)" "$(uv python dir).nascosto"
trap 'mv "$(uv python dir).nascosto" "$(uv python dir)" 2>/dev/null || true' EXIT
PYTHONPATH="$MOVED/Contents/Resources/app" "$MOVED/Contents/Resources/python/bin/python3" -c "import numpy, scipy, tkinter; print('ok fuori posto')"
rm -rf "$(dirname "$MOVED")"
PYTHONPATH="$APP/Contents/Resources/app" "$PY" -c "
import numpy, scipy, certifi, tkinter
print('Tcl/Tk', tkinter.Tcl().eval('info patchlevel'))
import blackmix, blackmix.mixer, blackmix.updater
print('BlackMix', blackmix.__version__)
"

echo "==> Controllo compatibilita' con macOS $MIN_MACOS"
python3 "$ROOT/packaging/check_minos.py" "$APP" "$MIN_MACOS"

echo "==> Firma ad-hoc"
codesign --force --deep --sign - "$APP" || echo "(firma ad-hoc non riuscita, continuo)"

echo "==> Pacchetto"
# prima di installare rimuove la versione vecchia, cosi' non restano file orfani
cat > "$BUILD/scripts/preinstall" <<'EOF'
#!/bin/bash
TARGET="${3:-/}"
OLD="${TARGET%/}/Applications/BlackMix.app"
case "$OLD" in
  */Applications/BlackMix.app) rm -rf "$OLD" ;;
esac
exit 0
EOF
chmod +x "$BUILD/scripts/preinstall"

pkgbuild --analyze --root "$BUILD/root" "$BUILD/component.plist"
plutil -replace 0.BundleIsRelocatable -bool NO "$BUILD/component.plist"
pkgbuild --root "$BUILD/root" --component-plist "$BUILD/component.plist" \
  --scripts "$BUILD/scripts" --identifier "$ID" --version "$VERSION" \
  --install-location /Applications "$BUILD/BlackMix-component.pkg"

productbuild --synthesize --package "$BUILD/BlackMix-component.pkg" "$BUILD/distribution.xml"
# titolo e architettura nel programma di installazione
TITLE="$VERSION" HOSTARCH="$ARCH" MINOS="$MIN_MACOS" perl -0pi -e \
  's|(<installer-gui-script[^>]*>)|$1\n    <title>BlackMix $ENV{TITLE}</title>\n    <options hostArchitectures="$ENV{HOSTARCH}"/>\n    <volume-check><allowed-os-versions><os-version min="$ENV{MINOS}"/></allowed-os-versions></volume-check>|' \
  "$BUILD/distribution.xml"
OUT="$BUILD/BlackMix-$VERSION-$ARCH.pkg"
productbuild --distribution "$BUILD/distribution.xml" --package-path "$BUILD" "$OUT"
rm -f "$BUILD/BlackMix-component.pkg"
ls -lh "$OUT"
PKG_MB=$(du -sm "$OUT" | cut -f1)
[ "$PKG_MB" -gt 15 ] || { echo "Pacchetto troppo piccolo ($PKG_MB MB)"; exit 1; }
echo "PKG=$OUT"
