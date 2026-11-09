# BlackMix

Mix automatico per Mac di canzoni registrate in multitraccia. Apri un progetto
**Logic Pro** (`.logicx`) oppure una cartella di WAV/AIFF, BlackMix trova le
registrazioni, le allinea, riconosce gli strumenti e produce un mix stereo
pronto da ascoltare, con un report delle scelte fatte.

## Installazione

1. Scarica l'ultimo pacchetto dalla pagina
   [Releases](https://github.com/SculptWorksLTD/BlackMix/releases):
   - `BlackMix-x.y.z-arm64.pkg` per i Mac Apple Silicon (M1, M2…), macOS 12 o successivo
   - `BlackMix-x.y.z-x86_64.pkg` per i Mac Intel, da **macOS 10.15 Catalina** in poi
2. Aprilo e segui l'Installer: BlackMix finisce in **Applicazioni**.
   Non serve installare Python: è tutto dentro l'app.

Il pacchetto non è firmato con un certificato Apple, quindi la prima volta
macOS lo blocca: vai in **Impostazioni di Sistema > Privacy e sicurezza** e
premi **Apri comunque** accanto al messaggio su BlackMix.

### Aggiornamenti automatici

All'avvio BlackMix controlla se su GitHub c'è una release più nuova. Se c'è,
chiede se aggiornare, scarica il `.pkg` giusto e apre l'Installer. Il controllo
si può fare anche a mano con il pulsante **Aggiornamenti**.

### Pubblicare una nuova versione

1. Aggiorna `__version__` in `blackmix/__init__.py` (es. `0.2.0`).
2. Fai commit e push su `main`.
3. GitHub Actions vede che la versione `v0.2.0` non esiste ancora, costruisce
   l'app su un Mac, la prova con un mix di test e pubblica la release con il
   `.pkg` (crea anche il tag). Da quel momento le app installate propongono
   l'aggiornamento. I push che non cambiano versione non pubblicano nulla.

Il workflow si può anche avviare a mano da **Actions > Build e release > Run
workflow**: in quel caso costruisce il `.pkg` senza pubblicarlo (si scarica
dagli artifact).

### Dal codice sorgente

Con Python 3 di python.org installato, doppio clic su `BlackMix.command`
(la prima volta: clic destro > Apri). Da terminale, senza interfaccia:

```bash
python3 -m blackmix.cli "Canzone.logicx" --genere Rock --lufs -14
python3 -m blackmix.cli "Canzone.logicx" --elenca     # mostra solo le tracce trovate
```

## Come si usa

1. **Apri progetto Logic…** e scegli il `.logicx` (oppure **Apri cartella…** se il
   progetto è salvato come cartella o se hai una cartella di tracce esportate).
2. Controlla l'elenco: per ogni file puoi
   - spuntarlo o no,
   - correggere il **ruolo** (voce, cassa, basso, chitarra…),
   - cambiare l'**inizio** in secondi,
   - alzare o abbassare il **volume** di quella traccia.
3. Scegli genere, loudness finale, quanto mettere avanti la voce, quanto riverbero
   e quanta compressione.
4. **Crea il mix**, poi **▶ Ascolta**. Il file finisce in `~/Music/BlackMix`,
   con accanto il report `_report.txt`.

## Cosa fa il mix

| Passaggio | Come |
|---|---|
| Allineamento | Timestamp delle registrazioni di Logic (chunk BWF `bext`) |
| Take multiple | `Audio 1#01`, `#02`…: tiene l'ultima, le altre restano deselezionate |
| Riconoscimento strumenti | Dal nome (italiano e inglese), altrimenti un'ipotesi dal suono |
| Polarità | Inverte un microfono se è in controfase con un altro dello stesso strumento (es. cassa dentro/fuori) |
| EQ | Passa-alto e correzioni tipiche per ruolo |
| EQ anti-mascheramento | Le tracce meno importanti cedono spazio nelle frequenze chiave di voce, cassa, basso, rullante |
| Compressione | Per ruolo, con soglia calcolata sul livello reale della traccia |
| De-esser | Su voce e cori |
| Volumi | In loudness (LUFS) relativa alla voce, divisa tra le tracce dello stesso ruolo |
| Panorama | Voce, cassa, basso, rullante al centro; chitarre doppie a sinistra/destra; cori e tom distribuiti; `L`/`R`/`sx`/`dx` nel nome rispettati |
| Riverbero | Un bus condiviso con mandate per ruolo |
| Master | Compressore di bus leggero, limiter true-peak, loudness obiettivo (EBU R128) |

Il report riporta tutto in una forma riproducibile in Logic (Channel EQ,
Compressor, pan in valori Logic da -64 a +63), così puoi usare BlackMix anche
come punto di partenza per un mix fatto a mano.

## Limiti

- Legge le **registrazioni originali** dentro il progetto: tagli, dissolvenze,
  comping delle take, region spostate, plugin e automazioni di Logic **non**
  vengono letti. Se hai già fatto molto editing, esporta le tracce da Logic
  (File > Esporta > Tutte le tracce come file audio, dall'inizio del progetto)
  e apri quella cartella.
- I file senza timestamp (per esempio importati) partono da 0: correggi
  l'inizio a mano se serve.
- Formati letti direttamente: WAV/BWF e AIFF. Per CAF e altri serve `ffmpeg`
  (`brew install ffmpeg`).

## Struttura

```
blackmix/
  audio_io.py   lettura/scrittura WAV e AIFF, timestamp BWF
  dsp.py        filtri, loudness, compressore, de-esser, riverbero, limiter
  roles.py      ruoli degli strumenti, preset di genere, riconoscimento dal nome
  project.py    ricerca dei file nel progetto Logic, take, allineamento
  mixer.py      analisi, decisioni di mix, elaborazione, report
  app.py        interfaccia grafica
  cli.py        uso da terminale
  updater.py    aggiornamenti dalle release di GitHub
packaging/
  build_pkg.sh  costruisce BlackMix.app e il .pkg (su macOS)
  Info.plist    modello per l'app
  make_icon.py  genera l'icona BlackMix.icns
  check_minos.py verifica che ogni binario dell'app giri sul macOS minimo
.github/workflows/release.yml   build automatica e release
strumenti/
  crea_progetto_prova.py   genera un finto progetto Logic per le prove
```
