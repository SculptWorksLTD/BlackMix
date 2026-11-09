#!/bin/bash
# BlackMix - doppio clic per avviare.
# Al primo avvio crea un ambiente Python dedicato e installa numpy e scipy.

cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
ENV_DIR="$HOME/Library/Application Support/BlackMix/venv"

avviso() {
  osascript -e "display dialog \"$1\" with title \"BlackMix\" buttons {\"OK\"} default button 1" >/dev/null 2>&1
  echo "$1"
}

# Cerco un Python 3 con tkinter (quello di python.org lo include)
PY=""
for cand in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$cand" ] && "$cand" -c "import tkinter" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done

if [ -z "$PY" ]; then
  avviso "Serve Python 3 con l'interfaccia grafica (tkinter). Scaricalo da python.org (Downloads > macOS), installalo e riapri BlackMix. Se usi Homebrew: brew install python-tk"
  exit 1
fi

if [ ! -x "$ENV_DIR/bin/python" ]; then
  echo "Primo avvio: preparo l'ambiente (serve internet, un minuto circa)..."
  mkdir -p "$(dirname "$ENV_DIR")"
  "$PY" -m venv "$ENV_DIR" || { avviso "Non riesco a creare l'ambiente Python."; exit 1; }
  "$ENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
  "$ENV_DIR/bin/python" -m pip install -r "$HERE/requirements.txt" || {
    rm -rf "$ENV_DIR"
    avviso "Installazione di numpy/scipy non riuscita. Controlla la connessione e riprova."
    exit 1
  }
fi

cd "$HERE" && exec "$ENV_DIR/bin/python" -m blackmix "$@"
