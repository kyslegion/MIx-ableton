from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from automix.als import parse_als, apply_mix_copy, make_render_source_copy
from automix.audio import scan_folder
from automix.mix_engine import propose_local_mix, build_ai_payload
from automix.codex_adapter import ask_codex, codex_available
from automix.quality import evaluate_balance
from automix.ableton_control import export_individual_tracks, AbletonAutomationError, find_live_window
from automix.self_repair import run_self_repair, restore_latest_backup
from automix.updater import check_for_update, download_update, launch_update_worker, manifest_url

ROOT = Path(__file__).resolve().parent
DEFAULT_ALS = ROOT / "sample_project" / "24-chorus chateau.als"
WORKSPACES = ROOT / "AutoMix_Projects"
WORKSPACES.mkdir(exist_ok=True)
APP_VERSION = 11


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ableton AutoMix V11 — export automatique + mix + auto-réparation")
        self.geometry("1180x760")
        self.minsize(980, 650)

        self.source_path: Path | None = None
        self.current_mix_path: Path | None = None
        self.project = None
        self.metrics = []
        self.previous_metrics = []
        self.actions: list[dict] = []
        self.pass_index = 0
        self.workspace: Path | None = None
        self.busy = False
        self.feedback_images: list[Path] = []
        self.last_error_text = ""
        self.auto_restart_var = tk.BooleanVar(value=True)

        self._build_ui()
        last_file = ROOT / ".last_project.txt"
        initial = DEFAULT_ALS
        try:
            if last_file.exists():
                candidate = Path(last_file.read_text(encoding="utf-8").strip())
                if candidate.exists() and candidate.suffix.lower() == ".als":
                    initial = candidate
        except Exception:
            pass
        if initial.exists():
            self.load_project(initial)
        # Vérification silencieuse au démarrage une fois une source de mises à jour configurée.
        if manifest_url(ROOT):
            self.after(2500, self.check_updates)

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Button(top, text="Changer de .als", command=self.choose_project).pack(side="left")
        self.project_label = ttk.Label(top, text="Aucun projet")
        self.project_label.pack(side="left", padx=12)
        self.update_button = ttk.Button(top, text="Mises à jour", command=self.check_updates)
        self.update_button.pack(side="right", padx=(8, 0))
        self.codex_label = ttk.Label(top, text="")
        self.codex_label.pack(side="right")
        self.codex_label.configure(text="Codex connecté/détecté" if codex_available() else "Codex non détecté — moteur local actif")

        intro = ttk.Label(
            self,
            padding=(10, 0, 10, 10),
            text=(
                "Principe : AutoMix pilote l’export des stems dans Ableton, analyse les WAV, crée une copie mixée, "
                "puis peut refaire une passe de contrôle. Si le bouton Exporter résiste, tu peux cliquer dessus toi-même : "
                "la V11 reste en veille et reprend automatiquement dès que la fenêtre Enregistrer apparaît."
            ),
            wraplength=1120,
        )
        intro.pack(fill="x")

        flow = ttk.Frame(self, padding=10)
        flow.pack(fill="x")
        self.btn1 = ttk.Button(flow, text="1. CRÉER LES STEMS + LES ANALYSER", command=self.step1)
        self.btn1.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        self.btn2 = ttk.Button(flow, text="2. CRÉER LE MIX AUTOMATIQUE", command=self.step2)
        self.btn2.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.btn3 = ttk.Button(flow, text="3. RENDRE + VÉRIFIER + PRÉPARER LA PASSE SUIVANTE", command=self.step3)
        self.btn3.grid(row=0, column=2, padx=5, pady=5, sticky="ew")
        flow.columnconfigure((0, 1, 2), weight=1)

        self.status = ttk.Label(self, text="Prêt", padding=(10, 0, 10, 8))
        self.status.pack(fill="x")

        nb = ttk.Notebook(self)
        self.notebook = nb
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tab_tracks = ttk.Frame(nb, padding=8)
        self.tab_audio = ttk.Frame(nb, padding=8)
        self.tab_actions = ttk.Frame(nb, padding=8)
        self.tab_feedback = ttk.Frame(nb, padding=10)
        nb.add(self.tab_tracks, text="Projet")
        nb.add(self.tab_audio, text="Mesures")
        nb.add(self.tab_actions, text="Décisions de mix")
        nb.add(self.tab_feedback, text="Correction / aide")

        cols = ("type", "name", "db", "pan", "devices")
        self.track_tree = ttk.Treeview(self.tab_tracks, columns=cols, show="headings")
        for c, h, w in [
            ("type", "Type", 90), ("name", "Piste", 240), ("db", "Fader dB", 90),
            ("pan", "Pan", 70), ("devices", "Devices", 600),
        ]:
            self.track_tree.heading(c, text=h); self.track_tree.column(c, width=w, anchor="w")
        self.track_tree.pack(fill="both", expand=True)

        acols = ("file", "lufs", "rms", "peak", "crest", "centroid", "stereo")
        self.audio_tree = ttk.Treeview(self.tab_audio, columns=acols, show="headings")
        for c, h, w in [
            ("file", "Stem", 390), ("lufs", "LUFS-I", 80), ("rms", "RMS", 80),
            ("peak", "Peak", 80), ("crest", "Crest", 80), ("centroid", "Centroid Hz", 100),
            ("stereo", "Corr. stéréo", 100),
        ]:
            self.audio_tree.heading(c, text=h); self.audio_tree.column(c, width=w, anchor="w")
        self.audio_tree.pack(fill="both", expand=True)

        mcols = ("track", "gain", "pan", "confidence", "reason")
        self.mix_tree = ttk.Treeview(self.tab_actions, columns=mcols, show="headings")
        for c, h, w in [
            ("track", "Piste", 220), ("gain", "Δ gain dB", 95), ("pan", "Pan", 70),
            ("confidence", "Confiance", 90), ("reason", "Pourquoi", 650),
        ]:
            self.mix_tree.heading(c, text=h); self.mix_tree.column(c, width=w, anchor="w")
        self.mix_tree.pack(fill="both", expand=True)

        # --- Self-repair / feedback tab -----------------------------------
        ttk.Label(
            self.tab_feedback,
            text=("Si quelque chose bloque, écris ici ce qui s'est passé et/ou ajoute une capture. "
                  "Le bouton Auto-corriger envoie le diagnostic et les images à Codex, qui répare "
                  "une copie isolée du code, la teste, puis applique uniquement les fichiers du logiciel."),
            wraplength=1080,
        ).pack(fill="x", pady=(0, 8))

        feedback_body = ttk.Panedwindow(self.tab_feedback, orient="horizontal")
        feedback_body.pack(fill="both", expand=True)
        text_frame = ttk.Labelframe(feedback_body, text="Ce que tu veux corriger", padding=8)
        img_frame = ttk.Labelframe(feedback_body, text="Captures jointes", padding=8)
        feedback_body.add(text_frame, weight=3)
        feedback_body.add(img_frame, weight=2)

        self.feedback_text = tk.Text(text_frame, wrap="word", height=12)
        self.feedback_text.pack(fill="both", expand=True)
        self.feedback_text.insert("1.0", "Décris ici ce qui ne marche pas, ou laisse simplement la capture parler.\n")

        self.feedback_list = tk.Listbox(img_frame, height=8)
        self.feedback_list.pack(fill="both", expand=True)
        self.feedback_list.bind("<Control-v>", lambda _e: (self.paste_feedback_image(), "break")[1])

        img_buttons = ttk.Frame(img_frame)
        img_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(img_buttons, text="Capturer Ableton", command=self.capture_ableton_feedback).pack(side="left")
        ttk.Button(img_buttons, text="Coller capture", command=self.paste_feedback_image).pack(side="left", padx=5)
        ttk.Button(img_buttons, text="Ajouter image…", command=self.choose_feedback_images).pack(side="left")
        ttk.Button(img_buttons, text="Retirer", command=self.remove_feedback_image).pack(side="right")

        repair_row = ttk.Frame(self.tab_feedback)
        repair_row.pack(fill="x", pady=(10, 0))
        self.btn_repair = ttk.Button(repair_row, text="AUTO-CORRIGER LE LOGICIEL AVEC CODEX", command=self.start_self_repair)
        self.btn_repair.pack(side="left", ipadx=15, ipady=4)
        ttk.Checkbutton(repair_row, text="Relancer automatiquement après correction", variable=self.auto_restart_var).pack(side="left", padx=12)
        ttk.Button(repair_row, text="Annuler le dernier correctif", command=self.restore_last_repair).pack(side="right")
        self.repair_status = ttk.Label(self.tab_feedback, text="Aucun correctif en cours.")
        self.repair_status.pack(fill="x", pady=(7, 0))

        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Ouvrir le dossier de travail", command=self.open_workspace).pack(side="left")
        ttk.Button(bottom, text="Ouvrir le meilleur .als", command=self.open_current_mix).pack(side="left", padx=8)
        self.quality_label = ttk.Label(bottom, text="Qualité mesurée : —")
        self.quality_label.pack(side="left", padx=15)

        self.log = tk.Text(self, height=9, wrap="word")
        self.log.pack(fill="x", padx=10, pady=(0, 10))
        self._log("V11 prête. Tout est enregistré automatiquement dans AutoMix_Projects\\<nom du projet>\\session_... : stems, rendus, rapports et copies .als. L’original .als ne sera jamais modifié.")

    def _log(self, msg: str):
        def write():
            self.log.insert("end", time.strftime("%H:%M:%S") + "  " + str(msg) + "\n")
            self.log.see("end")
        if threading.current_thread() is threading.main_thread():
            write()
        else:
            self.after(0, write)

    def _set_busy(self, yes: bool, text: str | None = None):
        self.busy = yes
        state = "disabled" if yes else "normal"
        for b in (self.btn1, self.btn2, self.btn3):
            b.configure(state=state)
        if text:
            self.status.configure(text=text)

    def choose_project(self):
        p = filedialog.askopenfilename(filetypes=[("Ableton Live Set", "*.als")])
        if p:
            self.load_project(Path(p))

    def load_project(self, path: Path):
        try:
            project = parse_als(path)
        except Exception as exc:
            messagebox.showerror("Projet", str(exc)); return
        self.source_path = Path(path).resolve()
        try:
            (ROOT / ".last_project.txt").write_text(str(self.source_path), encoding="utf-8")
        except Exception:
            pass
        self.current_mix_path = None
        self.project = project
        self.metrics = []
        self.previous_metrics = []
        self.actions = []
        self.pass_index = 0
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.source_path.stem)
        project_root = WORKSPACES / safe
        self.workspace = project_root / f"session_{stamp}"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.project_label.configure(text=str(self.source_path))
        self._refresh_tracks(project)
        self._clear_audio_actions()
        self._log(f"Projet chargé : {self.source_path.name} — {len(project.tracks)} piste(s).")
        self._save_json("project_source.json", project.to_dict())
        self.status.configure(text="Étape suivante : 1. Créer les stems")

    def _refresh_tracks(self, project):
        for i in self.track_tree.get_children(): self.track_tree.delete(i)
        for t in project.tracks:
            self.track_tree.insert("", "end", values=(t.track_type, t.name, f"{t.volume_db:.2f}", f"{t.pan:.2f}", ", ".join(t.devices)))

    def _clear_audio_actions(self):
        for tree in (self.audio_tree, self.mix_tree):
            for i in tree.get_children(): tree.delete(i)
        self.quality_label.configure(text="Qualité mesurée : —")

    def _set_metrics(self, metrics):
        self.metrics = metrics
        for i in self.audio_tree.get_children(): self.audio_tree.delete(i)
        for m in metrics:
            self.audio_tree.insert("", "end", values=(
                Path(m.file).name,
                "—" if m.lufs_i is None else f"{m.lufs_i:.1f}",
                f"{m.rms_dbfs:.1f}", f"{m.peak_dbfs:.1f}", f"{m.crest_db:.1f}",
                f"{m.spectral_centroid_hz:.0f}",
                "—" if m.stereo_correlation is None else f"{m.stereo_correlation:.2f}",
            ))
        if self.project:
            q = evaluate_balance(self.project, metrics)
            self.quality_label.configure(text=f"Qualité mesurée : {q.score}/100 — {q.summary}")
            self._save_json(f"pass_{self.pass_index:02d}_quality.json", q.to_dict())
        self._save_json(f"pass_{self.pass_index:02d}_audio.json", [m.to_dict() for m in metrics])

    def _set_actions(self, actions: list[dict], source: str):
        clean = []
        for a in actions:
            try:
                clean.append({
                    "track_id": str(a.get("track_id", "")),
                    "track_name": str(a.get("track_name", "")),
                    "stem_file": a.get("stem_file"),
                    "gain_db_delta": max(-4.0, min(4.0, float(a.get("gain_db_delta", 0.0)))),
                    "pan": None if a.get("pan") is None else max(-0.35, min(0.35, float(a.get("pan")))),
                    "reason": str(a.get("reason", "")),
                    "confidence": max(0.0, min(1.0, float(a.get("confidence", 0.5)))),
                })
            except Exception:
                pass
        self.actions = clean
        for i in self.mix_tree.get_children(): self.mix_tree.delete(i)
        for a in clean:
            self.mix_tree.insert("", "end", values=(
                a["track_name"], f'{a["gain_db_delta"]:+.2f}',
                "—" if a["pan"] is None else f'{a["pan"]:+.2f}',
                f'{a["confidence"]:.2f}', a["reason"],
            ))
        self._save_json(f"pass_{self.pass_index:02d}_actions.json", clean)
        self._log(f"Décisions calculées par {source} : {len(clean)} piste(s).")

    def _save_json(self, name: str, data):
        if not self.workspace: return
        try:
            (self.workspace / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def step1(self):
        if self.busy or not self.source_path or not self.workspace: return
        self._set_busy(True, "Étape 1 en cours : Ableton crée les stems…")
        threading.Thread(target=self._step1_worker, daemon=True).start()

    def _step1_worker(self):
        try:
            render_set = self.workspace / "render_source_pass_00.als"
            render_set, end_beats = make_render_source_copy(self.source_path, render_set, tail_beats=4.0)
            self._log(f"Copie de rendu préparée avec une sélection 0 → {end_beats:.1f} temps.")
            stems = self.workspace / "pass_00_stems"
            self._log(f"Dossier d’enregistrement automatique : {stems}")
            result = export_individual_tracks(
                render_set, stems,
                expected_min_files=max(1, len(self.project.tracks) if self.project else 1),
                log=self._log,
            )
            metrics = scan_folder(stems)
            self.previous_metrics = []
            self.pass_index = 0
            self.after(0, lambda: self._set_metrics(metrics))
            self.after(0, lambda: self.status.configure(text=f"Étape 1 terminée : {len(metrics)} stem(s). Clique sur 2."))
            self._log("Les stems ont été créés par Ableton et analysés automatiquement.")
        except Exception as exc:
            self._handle_worker_error("Création des stems", exc)
        finally:
            self.after(0, lambda: self._set_busy(False))

    def step2(self):
        if self.busy: return
        if not self.source_path or not self.workspace or not self.metrics:
            messagebox.showwarning("Étape 2", "Fais d'abord l'étape 1 pour que les stems soient créés et analysés.")
            return
        self._set_busy(True, "Étape 2 : calcul du mix et création d'une copie .als…")
        threading.Thread(target=self._step2_worker, daemon=True).start()

    def _choose_actions(self):
        # Codex is used when available; local engine is the deterministic fallback.
        if codex_available():
            try:
                self._log("Je demande à Codex une première décision de balance à partir des mesures…")
                payload = build_ai_payload(self.project, self.metrics)
                acts = ask_codex(payload, ROOT)
                if acts:
                    return acts, "Codex"
            except Exception as exc:
                self._log(f"Codex indisponible pour cette passe ({exc}). Je bascule sur le moteur local.")
        acts = [a.to_dict() for a in propose_local_mix(self.project, self.metrics)]
        return acts, "moteur local"

    def _step2_worker(self):
        try:
            acts, source = self._choose_actions()
            self.after(0, lambda a=acts, s=source: self._set_actions(a, s))
            if not acts:
                raise RuntimeError("Aucune correction exploitable n'a été calculée.")
            self.pass_index = 1
            out = self.workspace / "AUTOMIX_PASS_01.als"
            apply_mix_copy(self.source_path, out, acts)
            self.current_mix_path = out
            self.project = parse_als(out)
            self.after(0, lambda: self._refresh_tracks(self.project))
            self._log(f"Mix 1 créé automatiquement : {out.name}")
            self.after(0, lambda: self.status.configure(text="Mix 1 prêt. Clique sur 3 pour que je le rende, le réécoute par mesures et prépare la suite."))
        except Exception as exc:
            self._handle_worker_error("Création du mix", exc)
        finally:
            self.after(0, lambda: self._set_busy(False))

    def step3(self):
        if self.busy: return
        if not self.current_mix_path or not self.workspace:
            messagebox.showwarning("Étape 3", "Crée d'abord le mix automatique avec l'étape 2.")
            return
        self._set_busy(True, f"Contrôle de la passe {self.pass_index:02d}…")
        threading.Thread(target=self._step3_worker, daemon=True).start()

    def _step3_worker(self):
        try:
            current_pass = self.pass_index
            render_set = self.workspace / f"render_source_pass_{current_pass:02d}.als"
            render_set, _ = make_render_source_copy(self.current_mix_path, render_set, tail_beats=4.0)
            stems = self.workspace / f"pass_{current_pass:02d}_stems"
            export_individual_tracks(
                render_set, stems,
                expected_min_files=max(1, len(self.project.tracks) if self.project else 1),
                log=self._log,
            )
            new_metrics = scan_folder(stems)
            old_q = evaluate_balance(self.project, self.metrics) if self.metrics else None
            self.previous_metrics = self.metrics
            self.metrics = new_metrics
            new_q = evaluate_balance(self.project, new_metrics)
            self.after(0, lambda: self._set_metrics(new_metrics))
            if old_q:
                self._log(f"Comparaison : {old_q.score}/100 → {new_q.score}/100.")

            if new_q.stable:
                self._log("La balance mesurée est devenue stable. Je garde cette passe comme résultat courant.")
                self.after(0, lambda: self.status.configure(text=f"Mix stable à la passe {current_pass:02d}. Tu peux ouvrir le meilleur .als."))
                return

            acts, source = self._choose_actions()
            self.after(0, lambda a=acts, s=source: self._set_actions(a, s))
            meaningful = [a for a in acts if abs(float(a.get("gain_db_delta", 0))) >= 0.25 or a.get("pan") is not None]
            if not meaningful:
                self._log("Aucune correction supplémentaire significative. Je conserve cette passe.")
                self.after(0, lambda: self.status.configure(text=f"Mix terminé à la passe {current_pass:02d}."))
                return

            next_pass = current_pass + 1
            out = self.workspace / f"AUTOMIX_PASS_{next_pass:02d}.als"
            apply_mix_copy(self.current_mix_path, out, acts)
            self.current_mix_path = out
            self.pass_index = next_pass
            self.project = parse_als(out)
            self.after(0, lambda: self._refresh_tracks(self.project))
            self._log(f"Passe suivante préparée automatiquement : {out.name}")
            self.after(0, lambda: self.status.configure(text=f"Passe {next_pass:02d} prête. Reclique sur 3 pour la rendre et la contrôler."))
        except Exception as exc:
            self._handle_worker_error("Contrôle du mix", exc)
        finally:
            self.after(0, lambda: self._set_busy(False))

    def _handle_worker_error(self, title: str, exc: Exception):
        self._log(f"ERREUR : {exc}")
        msg = str(exc)
        if isinstance(exc, AbletonAutomationError) and self.workspace:
            msg += f"\n\nLe diagnostic Ableton est conservé dans le dossier de travail :\n{self.workspace}"
        self.last_error_text = f"{title}\n{msg}"
        self.after(0, lambda t=title, m=msg: self._prepare_feedback_from_error(t, m))
        self.after(0, lambda: self.status.configure(text="Étape interrompue — l'erreur a été envoyée dans Correction / aide."))

    def _feedback_attach_dir(self) -> Path:
        base = self.workspace if self.workspace else (ROOT / ".feedback")
        out = Path(base) / "feedback_attachments"
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _add_feedback_image(self, src: Path, *, copy_file: bool = True) -> Path | None:
        try:
            src = Path(src)
            if not src.exists():
                return None
            if copy_file:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                dst = self._feedback_attach_dir() / f"capture_{stamp}_{len(self.feedback_images)+1:02d}{src.suffix.lower() or '.png'}"
                shutil.copy2(src, dst)
            else:
                dst = src
            if dst not in self.feedback_images:
                self.feedback_images.append(dst)
                self.feedback_list.insert("end", dst.name)
            return dst
        except Exception as exc:
            messagebox.showerror("Capture", str(exc))
            return None

    def choose_feedback_images(self):
        files = filedialog.askopenfilenames(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Tous les fichiers", "*.*")])
        for f in files:
            self._add_feedback_image(Path(f), copy_file=True)

    def paste_feedback_image(self):
        try:
            from PIL import Image, ImageGrab
            data = ImageGrab.grabclipboard()
            if isinstance(data, Image.Image):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                dst = self._feedback_attach_dir() / f"clipboard_{stamp}.png"
                data.save(dst)
                self._add_feedback_image(dst, copy_file=False)
                self.repair_status.configure(text=f"Capture collée : {dst.name}")
                return
            if isinstance(data, list):
                added = 0
                for raw in data:
                    p = Path(raw)
                    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        if self._add_feedback_image(p, copy_file=True):
                            added += 1
                if added:
                    self.repair_status.configure(text=f"{added} image(s) ajoutée(s) depuis le presse-papiers.")
                    return
            messagebox.showinfo("Coller capture", "Le presse-papiers ne contient pas d'image. Copie une capture puis reclique ici.")
        except Exception as exc:
            messagebox.showerror("Coller capture", f"Impossible de lire l'image du presse-papiers : {exc}")

    def capture_ableton_feedback(self, silent: bool = False):
        try:
            w = find_live_window(self.source_path.stem if self.source_path else None)
            if w is None:
                raise RuntimeError("Je ne trouve pas de fenêtre Ableton Live ouverte.")
            img = w.capture_as_image()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dst = self._feedback_attach_dir() / f"ableton_{stamp}.png"
            img.save(dst)
            self._add_feedback_image(dst, copy_file=False)
            self.repair_status.configure(text=f"Capture Ableton ajoutée : {dst.name}")
            return dst
        except Exception as exc:
            if not silent:
                messagebox.showerror("Capturer Ableton", str(exc))
            return None

    def remove_feedback_image(self):
        sel = list(self.feedback_list.curselection())
        for idx in reversed(sel):
            try:
                self.feedback_images.pop(idx)
            except Exception:
                pass
            self.feedback_list.delete(idx)

    def _prepare_feedback_from_error(self, title: str, msg: str):
        try:
            current = self.feedback_text.get("1.0", "end").strip()
            prefix = "Décris ici ce qui ne marche pas, ou laisse simplement la capture parler."
            if current == prefix:
                self.feedback_text.delete("1.0", "end")
            self.feedback_text.insert("end", f"\n[{time.strftime('%H:%M:%S')}] ERREUR — {title}\n{msg}\n")
            self.feedback_text.see("end")
            self.notebook.select(self.tab_feedback)
            # Capture the current Live state automatically; the user can add more images.
            self.capture_ableton_feedback(silent=True)
            messagebox.showerror(title, msg + "\n\nL'erreur et une capture d'Ableton ont été ajoutées à l'onglet Correction / aide. Clique sur AUTO-CORRIGER si tu veux que Codex adapte le logiciel directement.")
        except Exception:
            messagebox.showerror(title, msg)

    def start_self_repair(self):
        if self.busy:
            messagebox.showwarning("Auto-correction", "Attends la fin de l'étape en cours avant de réparer le logiciel.")
            return
        if not codex_available():
            messagebox.showwarning("Auto-correction", "Codex CLI n'est pas détecté. Lance CODEX_LOGIN.bat puis relance AutoMix.")
            return
        feedback = self.feedback_text.get("1.0", "end").strip()
        if not feedback and not self.feedback_images and not self.last_error_text:
            messagebox.showwarning("Auto-correction", "Écris le problème ou ajoute une capture avant de lancer la correction.")
            return
        runtime_log = self.log.get("1.0", "end")
        workspace = str(self.workspace) if self.workspace else None
        images = [str(p) for p in self.feedback_images]
        last_error = self.last_error_text
        auto_restart = bool(self.auto_restart_var.get())
        self._set_busy(True, "Codex répare une copie du logiciel…")
        self.btn_repair.configure(state="disabled")
        self.repair_status.configure(text="Création d'une copie isolée et analyse par Codex…")
        threading.Thread(
            target=self._self_repair_worker,
            args=(feedback, runtime_log, workspace, images, last_error, auto_restart),
            daemon=True,
        ).start()

    def _self_repair_worker(self, feedback: str, runtime_log: str, workspace: str | None, images: list[str], last_error: str, auto_restart: bool):
        try:
            combined = feedback
            if last_error and last_error not in combined:
                combined += "\n\nDERNIÈRE ERREUR AUTOMATIQUE :\n" + last_error
            result = run_self_repair(
                ROOT,
                workspace=workspace,
                user_text=combined,
                screenshots=images,
                runtime_log=runtime_log,
                log=self._log,
            )
            if not result.ok:
                self.after(0, lambda: self.repair_status.configure(text="Codex a analysé le problème mais n'a modifié aucun fichier."))
                self.after(0, lambda: messagebox.showinfo("Auto-correction", result.summary[-5000:]))
                return
            changed = ", ".join(result.changed_files)
            self._log(f"Auto-correction terminée. Fichiers modifiés : {changed}")
            self.after(0, lambda: self.repair_status.configure(text=f"Correctif appliqué : {changed}"))
            if auto_restart:
                self._log("Je relance AutoMix dans 2 secondes pour charger le nouveau code…")
                self.after(2000, self._restart_app)
            else:
                self.after(0, lambda: messagebox.showinfo("Auto-correction", "Correctif appliqué et validé. Relance AutoMix pour charger la nouvelle version du code."))
        except Exception as exc:
            self._log(f"AUTO-CORRECTION ÉCHOUÉE : {exc}")
            self.after(0, lambda e=str(exc): self.repair_status.configure(text="Échec de l'auto-correction."))
            self.after(0, lambda e=str(exc): messagebox.showerror("Auto-correction", e))
        finally:
            self.after(0, lambda: self._set_busy(False))
            self.after(0, lambda: self.btn_repair.configure(state="normal"))

    def restore_last_repair(self):
        try:
            restored = restore_latest_backup(ROOT, log=self._log)
            messagebox.showinfo("Restauration", f"Le code précédent a été restauré depuis :\n{restored}\n\nAutoMix va redémarrer.")
            self._restart_app()
        except Exception as exc:
            messagebox.showerror("Restauration", str(exc))

    def _restart_app(self):
        try:
            subprocess.Popen([sys.executable, str(ROOT / "automix_app.py")], cwd=str(ROOT))
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Redémarrage", f"Le correctif est appliqué mais je n'arrive pas à relancer automatiquement : {exc}")


    def check_updates(self):
        if self.busy:
            return
        self.update_button.configure(state="disabled")
        self._log("Recherche d’une mise à jour AutoMix…")
        threading.Thread(target=self._check_updates_worker, daemon=True).start()

    def _check_updates_worker(self):
        try:
            info = check_for_update(ROOT, APP_VERSION)
            if info is None:
                self._log("AutoMix V11 est déjà à jour.")
                self.after(0, lambda: messagebox.showinfo("Mises à jour", "AutoMix V11 est déjà à jour."))
                return
            note = f"\n\n{info.notes}" if info.notes else ""
            def ask():
                ok = messagebox.askyesno(
                    "Mise à jour AutoMix",
                    f"AutoMix V{info.version} est disponible.{note}\n\nTélécharger et installer maintenant ?",
                )
                if ok:
                    self._start_update_download(info)
            self.after(0, ask)
        except Exception as exc:
            msg = str(exc)
            self._log(f"Mise à jour : {msg}")
            self.after(0, lambda m=msg: messagebox.showinfo("Mises à jour", m))
        finally:
            self.after(0, lambda: self.update_button.configure(state="normal"))

    def _start_update_download(self, info):
        self._set_busy(True, f"Téléchargement d’AutoMix V{info.version}…")
        self.update_button.configure(state="disabled")
        threading.Thread(target=self._update_download_worker, args=(info,), daemon=True).start()

    def _update_download_worker(self, info):
        try:
            self._log(f"Téléchargement sécurisé d’AutoMix V{info.version}…")
            archive = download_update(info)
            self._log("Mise à jour téléchargée et vérifiée. AutoMix va se fermer, se mettre à jour et redémarrer.")
            launch_update_worker(ROOT, archive)
            self.after(400, self.destroy)
        except Exception as exc:
            msg = str(exc)
            self._log(f"ÉCHEC DE LA MISE À JOUR : {msg}")
            self.after(0, lambda m=msg: messagebox.showerror("Mise à jour", m))
            self.after(0, lambda: self._set_busy(False, "Prêt"))
            self.after(0, lambda: self.update_button.configure(state="normal"))

    def open_workspace(self):
        if not self.workspace: return
        try:
            os.startfile(str(self.workspace))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Dossier", str(exc))

    def open_current_mix(self):
        p = self.current_mix_path or self.source_path
        if not p: return
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Ableton", str(exc))


if __name__ == "__main__":
    App().mainloop()
