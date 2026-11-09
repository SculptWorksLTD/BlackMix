"""BlackMix - interfaccia grafica (tkinter)."""
from __future__ import annotations

import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, updater
from .mixer import MixSettings, analyze, render
from .project import scan
from .roles import GENRES, ROLE_ORDER, ROLES

APP_NAME = "BlackMix"
TARGETS = [
    ("-14 LUFS  (Spotify, YouTube)", -14.0),
    ("-11 LUFS  (forte)", -11.0),
    ("-9 LUFS  (molto forte)", -9.0),
    ("-16 LUFS  (dinamico, Apple Music)", -16.0),
]
ROLE_LABELS = [ROLES[k].label for k in ROLE_ORDER]
LABEL_TO_ROLE = {ROLES[k].label: k for k in ROLE_ORDER}


def default_out_dir():
    d = os.path.join(os.path.expanduser("~"), "Music", APP_NAME)
    return d


class Row:
    def __init__(self, parent, r, track, on_change):
        self.track = track
        self.inc = tk.BooleanVar(value=track.include)
        self.role = tk.StringVar(value=ROLES[track.role].label if track.info else "")
        self.offset = tk.StringVar(value=f"{track.offset:.2f}")
        self.adj = tk.StringVar(value=f"{track.adjust_db:+.1f}")
        st = "normal" if track.info and not track.error else "disabled"
        self.cb = ttk.Checkbutton(parent, variable=self.inc, state=st, command=on_change)
        self.cb.grid(row=r, column=0, padx=(6, 2), pady=1)
        ttk.Label(parent, text=track.name, width=28, anchor="w").grid(row=r, column=1, sticky="w")
        self.combo = ttk.Combobox(parent, textvariable=self.role, values=ROLE_LABELS, width=20,
                                  state="readonly" if st == "normal" else "disabled")
        self.combo.grid(row=r, column=2, padx=2)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._role_changed())
        ttk.Entry(parent, textvariable=self.offset, width=7, state=st).grid(row=r, column=3, padx=2)
        ttk.Spinbox(parent, textvariable=self.adj, from_=-12, to=12, increment=0.5, width=6,
                    state=st, format="%+.1f").grid(row=r, column=4, padx=2)
        a = track.analysis or {}
        lufs = a.get("lufs")
        info = ""
        if track.info:
            info = f"{track.info.duration:5.1f} s"
            if lufs is not None and lufs != float("-inf"):
                info += f"   {lufs:5.1f} LUFS"
        ttk.Label(parent, text=info, width=18, anchor="w").grid(row=r, column=5, sticky="w")
        note = "; ".join(track.notes)
        if track.role_source == "suono":
            note = ("ruolo indovinato dal suono; " + note).strip("; ")
        ttk.Label(parent, text=note, foreground="#8a6d00" if note else None,
                  anchor="w").grid(row=r, column=6, sticky="w", padx=(4, 6))

    def _role_changed(self):
        self.track.role_source = "utente"

    def apply(self):
        t = self.track
        if not t.info:
            return
        t.include = bool(self.inc.get())
        t.role = LABEL_TO_ROLE.get(self.role.get(), t.role)
        try:
            t.offset = max(0.0, float(self.offset.get().replace(",", ".")))
        except ValueError:
            pass
        try:
            t.adjust_db = float(self.adj.get().replace(",", "."))
        except ValueError:
            pass


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} {__version__} - mix automatico")
        root.geometry("1150x760")
        root.minsize(900, 600)
        self.q = queue.Queue()
        self.tracks = []
        self.sr = 48000
        self.rows = []
        self.project_path = None
        self.last_mix = None
        self.player = None
        self.busy = False
        self._build()
        self.root.after(100, self._poll)
        self.root.after(1500, lambda: self.check_updates(silent=True))
        root.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------------ UI
    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("aqua" if platform.system() == "Darwin" else "clam")
        except tk.TclError:
            pass
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=("Helvetica", 20, "bold")).pack(side="left")
        ttk.Button(top, text="Apri progetto Logic…", command=self.choose_logicx).pack(side="left", padx=(20, 4))
        ttk.Button(top, text="Apri cartella…", command=self.choose_folder).pack(side="left", padx=4)
        self.proj_lbl = ttk.Label(top, text="Nessun progetto aperto", foreground="#666")
        self.proj_lbl.pack(side="left", padx=12)
        ttk.Button(top, text="Aggiornamenti", command=lambda: self.check_updates(silent=False)).pack(side="right")
        ttk.Label(top, text=f"v{__version__}", foreground="#888").pack(side="right", padx=8)

        # elenco tracce
        mid = ttk.LabelFrame(self.root, text="Tracce", padding=4)
        mid.pack(fill="both", expand=True, padx=10, pady=4)
        canvas = tk.Canvas(mid, highlightthickness=0)
        sb = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        self.rows_frame = ttk.Frame(canvas)
        self.rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas = canvas
        canvas.bind_all("<MouseWheel>", self._wheel)

        # impostazioni
        opt = ttk.LabelFrame(self.root, text="Impostazioni del mix", padding=8)
        opt.pack(fill="x", padx=10, pady=4)
        self.genre = tk.StringVar(value="Pop")
        self.vocal = tk.DoubleVar(value=0.0)
        self.reverb = tk.DoubleVar(value=100.0)
        self.comp = tk.DoubleVar(value=100.0)
        self.target = tk.StringVar(value=TARGETS[0][0])
        ttk.Label(opt, text="Genere").grid(row=0, column=0, sticky="w")
        ttk.Combobox(opt, textvariable=self.genre, values=list(GENRES), state="readonly",
                     width=22).grid(row=0, column=1, sticky="w", padx=(4, 20))
        ttk.Label(opt, text="Loudness finale").grid(row=0, column=2, sticky="w")
        ttk.Combobox(opt, textvariable=self.target, values=[t[0] for t in TARGETS], state="readonly",
                     width=30).grid(row=0, column=3, sticky="w", padx=4)
        self._slider(opt, 1, "Voce (dB)", self.vocal, -4, 4, "{:+.1f}")
        self._slider(opt, 2, "Riverbero (%)", self.reverb, 0, 200, "{:.0f}")
        self._slider(opt, 3, "Compressione (%)", self.comp, 0, 150, "{:.0f}")

        # azioni
        act = ttk.Frame(self.root, padding=(10, 4))
        act.pack(fill="x")
        self.mix_btn = ttk.Button(act, text="Crea il mix", command=self.do_mix)
        self.mix_btn.pack(side="left")
        self.play_btn = ttk.Button(act, text="▶ Ascolta", command=self.play, state="disabled")
        self.play_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(act, text="■ Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.save_btn = ttk.Button(act, text="Salva una copia…", command=self.save_as, state="disabled")
        self.save_btn.pack(side="left", padx=4)
        self.reveal_btn = ttk.Button(act, text="Mostra nel Finder", command=self.reveal, state="disabled")
        self.reveal_btn.pack(side="left", padx=4)
        self.prog = ttk.Progressbar(act, length=220, mode="determinate", maximum=1.0)
        self.prog.pack(side="right")
        self.status = ttk.Label(act, text="Apri un progetto per iniziare", width=40, anchor="e")
        self.status.pack(side="right", padx=8)

        # report
        rep = ttk.LabelFrame(self.root, text="Report", padding=4)
        rep.pack(fill="both", expand=False, padx=10, pady=(4, 10))
        self.report = tk.Text(rep, height=12, wrap="none", font=("Menlo", 11))
        rsb = ttk.Scrollbar(rep, orient="vertical", command=self.report.yview)
        self.report.configure(yscrollcommand=rsb.set)
        self.report.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")
        self._set_report("Apri il pacchetto .logicx del progetto (oppure una cartella di WAV/AIFF).\n"
                         "BlackMix trova le registrazioni, le allinea con i timestamp di Logic,\n"
                         "riconosce gli strumenti dal nome e crea il mix.")

    def _slider(self, parent, r, text, var, lo, hi, fmt):
        ttk.Label(parent, text=text).grid(row=r, column=0, sticky="w", pady=2)
        val = ttk.Label(parent, text=fmt.format(var.get()), width=6)
        s = ttk.Scale(parent, from_=lo, to=hi, variable=var, length=320,
                      command=lambda v: val.configure(text=fmt.format(float(v))))
        s.grid(row=r, column=1, columnspan=2, sticky="w", padx=4)
        val.grid(row=r, column=3, sticky="w")

    def _wheel(self, e):
        if self.canvas.winfo_containing(e.x_root, e.y_root) is None:
            return
        delta = -e.delta if platform.system() == "Darwin" else -e.delta // 120
        self.canvas.yview_scroll(int(delta), "units")

    def _set_report(self, txt):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", txt)
        self.report.configure(state="disabled")

    def _set_busy(self, busy):
        self.busy = busy
        self.mix_btn.configure(state="disabled" if busy else "normal")

    # ----------------------------------------------------------- progetto
    def choose_logicx(self):
        if self.busy:
            return
        p = filedialog.askopenfilename(title="Scegli il progetto Logic",
                                       filetypes=[("Progetto Logic", "*.logicx"), ("Tutti i file", "*")])
        if not p:
            # su alcune versioni di macOS i pacchetti si scelgono come cartelle
            return
        self.load_project(p)

    def choose_folder(self):
        if self.busy:
            return
        p = filedialog.askdirectory(title="Scegli la cartella del progetto o delle tracce")
        if p:
            self.load_project(p)

    def load_project(self, path):
        self.project_path = path.rstrip("/")
        self.proj_lbl.configure(text=os.path.basename(self.project_path), foreground="")
        self._set_busy(True)
        self._clear_rows()
        self.status.configure(text="Cerco le tracce…")

        def work():
            try:
                tracks, sr = scan(self.project_path)
                analyze(tracks, sr, lambda f, m: self.q.put(("prog", f, m)))
                self.q.put(("loaded", tracks, sr))
            except Exception as e:
                self.q.put(("error", f"Errore nell'apertura del progetto:\n{e}", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _clear_rows(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.rows = []

    def _show_tracks(self):
        self._clear_rows()
        if not self.tracks:
            ttk.Label(self.rows_frame, text="Nessun file audio trovato in questo progetto.").grid(row=0, column=0)
            return
        for i, txt in enumerate(["Usa", "File", "Ruolo", "Inizio (s)", "Volume dB",
                                 "Durata / loudness", "Note"]):
            ttk.Label(self.rows_frame, text=txt, font=("Helvetica", 11, "bold")).grid(
                row=0, column=i, sticky="w", padx=2, pady=(0, 4))
        for i, t in enumerate(self.tracks):
            self.rows.append(Row(self.rows_frame, i + 1, t, lambda: None))
        n = sum(1 for t in self.tracks if t.include)
        self.status.configure(text=f"{len(self.tracks)} file trovati, {n} selezionati")

    # ---------------------------------------------------------------- mix
    def settings(self):
        target = dict(TARGETS).get(self.target.get(), -14.0)
        return MixSettings(genre=self.genre.get(), vocal_db=round(self.vocal.get(), 1),
                           reverb=self.reverb.get() / 100.0, compression=self.comp.get() / 100.0,
                           target_lufs=target)

    def do_mix(self):
        if self.busy:
            return
        if not self.tracks:
            messagebox.showinfo(APP_NAME, "Prima apri un progetto.")
            return
        for r in self.rows:
            r.apply()
        if not any(t.include for t in self.tracks):
            messagebox.showinfo(APP_NAME, "Nessuna traccia selezionata.")
            return
        self.stop()
        name = os.path.splitext(os.path.basename(self.project_path))[0] or "Mix"
        out = os.path.join(default_out_dir(), f"{name} - BlackMix {time.strftime('%Y-%m-%d %H.%M.%S')}.wav")
        s = self.settings()
        tracks, sr = self.tracks, self.sr
        self._set_busy(True)

        def work():
            try:
                res = render(tracks, sr, s, out, lambda f, m: self.q.put(("prog", f, m)))
                self.q.put(("mixed", res))
            except Exception as e:
                self.q.put(("error", f"Errore durante il mix:\n{e}", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ ascolto
    def play(self):
        if not self.last_mix:
            return
        self.stop()
        cmd = None
        if platform.system() == "Darwin":
            cmd = ["afplay", self.last_mix]
        elif shutil.which("ffplay"):
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", self.last_mix]
        elif shutil.which("aplay"):
            cmd = ["aplay", "-q", self.last_mix]
        if not cmd:
            messagebox.showinfo(APP_NAME, "Nessun lettore audio trovato: apri il file dal Finder.")
            return
        self.player = subprocess.Popen(cmd)
        self.stop_btn.configure(state="normal")

    def stop(self):
        if self.player and self.player.poll() is None:
            self.player.terminate()
        self.player = None
        self.stop_btn.configure(state="disabled")

    def save_as(self):
        if not self.last_mix:
            return
        p = filedialog.asksaveasfilename(title="Salva il mix", defaultextension=".wav",
                                         initialfile=os.path.basename(self.last_mix),
                                         filetypes=[("WAV", "*.wav")])
        if p:
            shutil.copyfile(self.last_mix, p)
            rep = os.path.splitext(self.last_mix)[0] + "_report.txt"
            if os.path.exists(rep):
                shutil.copyfile(rep, os.path.splitext(p)[0] + "_report.txt")

    def reveal(self):
        if not self.last_mix:
            return
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", self.last_mix])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(self.last_mix)])

    # ------------------------------------------------------- aggiornamenti
    def check_updates(self, silent=True):
        def work():
            try:
                self.q.put(("update", updater.check(), silent))
            except Exception as e:
                if not silent:
                    self.q.put(("update_err", f"Non riesco a controllare gli aggiornamenti:\n{e}"))

        threading.Thread(target=work, daemon=True).start()

    def _offer_update(self, info, silent):
        if info is None:
            if not silent:
                messagebox.showinfo(APP_NAME, f"Hai gia' l'ultima versione ({__version__}).")
            return
        notes = info["notes"][:600]
        msg = f"E' disponibile BlackMix {info['version']} (hai la {__version__})."
        if notes:
            msg += "\n\nNovita':\n" + notes
        if not updater.is_bundled() or not info.get("url"):
            messagebox.showinfo(APP_NAME, msg + f"\n\nScaricala da:\n{info['page']}")
            return
        if not messagebox.askyesno(APP_NAME, msg + "\n\nScarico e installo ora?"):
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, "Aspetta che finisca il lavoro in corso, poi riprova.")
            return
        self._set_busy(True)
        self.status.configure(text="Scarico l'aggiornamento…")

        def work():
            try:
                path = updater.download(info, lambda f: self.q.put(("prog", f, "Scarico l'aggiornamento…")))
                self.q.put(("update_ready", path))
            except Exception as e:
                self.q.put(("update_err", f"Download non riuscito:\n{e}"))

        threading.Thread(target=work, daemon=True).start()

    # ----------------------------------------------------------- messaggi
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "prog":
                    self.prog["value"] = msg[1]
                    self.status.configure(text=msg[2][:60])
                elif kind == "loaded":
                    self.tracks, self.sr = msg[1], msg[2]
                    self._set_busy(False)
                    self._show_tracks()
                    self.prog["value"] = 0
                elif kind == "mixed":
                    res = msg[1]
                    self.last_mix = res["path"]
                    self._set_busy(False)
                    for b in (self.play_btn, self.save_btn, self.reveal_btn):
                        b.configure(state="normal")
                    m = res["master"]
                    self.status.configure(text=f"Fatto: {m['lufs']:.1f} LUFS, picco {m['peak_db']:.1f} dBFS")
                    self._set_report(f"Mix salvato in:\n{res['path']}\n\n" + res["report"])
                elif kind == "update":
                    self._offer_update(msg[1], msg[2])
                elif kind == "update_err":
                    self._set_busy(False)
                    self.prog["value"] = 0
                    self.status.configure(text="")
                    messagebox.showwarning(APP_NAME, msg[1])
                elif kind == "update_ready":
                    self.prog["value"] = 1.0
                    updater.open_installer(msg[1])
                    messagebox.showinfo(APP_NAME, "Si apre l'Installer: segui i passaggi.\n"
                                                  "BlackMix ora si chiude, riaprilo a installazione finita.")
                    self._quit()
                    return
                elif kind == "error":
                    self._set_busy(False)
                    self.prog["value"] = 0
                    self.status.configure(text="Errore")
                    self._set_report(msg[1] + "\n\n" + msg[2])
                    messagebox.showerror(APP_NAME, msg[1])
        except queue.Empty:
            pass
        if self.player and self.player.poll() is not None:
            self.player = None
            self.stop_btn.configure(state="disabled")
        self.root.after(100, self._poll)

    def _quit(self):
        self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        root.after(300, lambda: app.load_project(sys.argv[1]))
    root.mainloop()


if __name__ == "__main__":
    main()
