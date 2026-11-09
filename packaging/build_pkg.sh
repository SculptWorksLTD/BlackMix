#!/bin/bash
# Costruisce BlackMix.app (con Python, numpy e scipy dentro) e il pacchetto .pkg.
# Va eseguito su macOS. Uso: packaging/build_pkg.sh 0.1.0
set -euo pipefail

VERSION="${1:?Uso: build_pkg.sh VERSIONE}"
PY_VER="${PY_VER:-3.12}"
ARCH="$(uname -m)"                 # arm64 / x86_64
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
APP="$BUILD/root/BlackMix.app"
ID="com.sculptworks.blackmix"

command -v uv >/dev/null || { echo "Serve uv: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

rm -rf "$BUILD"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$BUILD/scripts"

echo "==> Python $PY_VER autonomo"
uv python install "$PY_VER"
PYHOME="$(ls -d "$(uv python dir)"/cpython-"$PY_VER"*-macos-* | grep -v freethreaded | head -1)"
[ -d "$PYHOME" ] || { echo "Python gestito da uv non trovato"; exit 1; }
cp -R "$PYHOME" "$APP/Contents/Resources/python"
PY="$APP/Contents/Resources/python/bin/python3"
rm -f "$APP"/Contents/Resources/python/lib/python3*/EXTERNALLY-MANAGED

echo "==> Librerie"
uv pip install --python "$PY" -r "$ROOT/requirements.txt" certifi
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

sed -e "s/@VERSION@/$VERSION/g" -e "s/@ID@/$ID/g" "$ROOT/packaging/Info.plist" > "$APP/Contents/Info.plist"
if [ -f "$ROOT/packaging/BlackMix.icns" ]; then
  cp "$ROOT/packaging/BlackMix.icns" "$APP/Contents/Resources/"
fi

echo "==> Prova del pacchetto"
PYTHONPATH="$APP/Contents/Resources/app" "$PY" -c "
import numpy, scipy, certifi, tkinter
print('Tcl/Tk', tkinter.Tcl().eval('info patchlevel'))
import blackmix, blackmix.mixer, blackmix.updater
print('BlackMix', blackmix.__version__)
"

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
TITLE="$VERSION" HOSTARCH="$ARCH" perl -0pi -e \
  's|(<installer-gui-script[^>]*>)|$1\n    <title>BlackMix $ENV{TITLE}</title>\n    <options hostArchitectures="$ENV{HOSTARCH}"/>|' \
  "$BUILD/distribution.xml"
OUT="$BUILD/BlackMix-$VERSION-$ARCH.pkg"
productbuild --distribution "$BUILD/distribution.xml" --package-path "$BUILD" "$OUT"
rm -f "$BUILD/BlackMix-component.pkg"
ls -lh "$OUT"
echo "PKG=$OUT"
