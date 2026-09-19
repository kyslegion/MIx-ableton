from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .codex_adapter import codex_available


ALLOWED_ROOT_FILES = [
    "automix_app.py",
    "requirements.txt",
    "README.txt",
    "RUN_AUTOMIX.bat",
    "install.bat",
    "CODEX_LOGIN.bat",
]


@dataclass
class RepairResult:
    ok: bool
    summary: str
    changed_files: list[str]
    backup_dir: str | None = None
    stage_dir: str | None = None


def _emit(log: Callable[[str], None] | None, msg: str) -> None:
    if log:
        log(msg)


def _iter_allowed_files(root: Path):
    for name in ALLOWED_ROOT_FILES:
        p = root / name
        if p.is_file():
            yield p, Path(name)
    pkg = root / "automix"
    if pkg.is_dir():
        for p in sorted(pkg.glob("*.py")):
            yield p, Path("automix") / p.name


def _copy_allowed_tree(root: Path, dst: Path) -> None:
    for src, rel in _iter_allowed_files(root):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def _latest_diagnostic(workspace: Path | None) -> Path | None:
    if not workspace or not workspace.exists():
        return None
    candidates = list(workspace.rglob("ableton_export_dialog.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _validate_python(stage: Path) -> tuple[bool, str]:
    files = [stage / "automix_app.py"] + sorted((stage / "automix").glob("*.py"))
    files = [p for p in files if p.exists()]
    if not files:
        return False, "Aucun fichier Python à valider."
    cmd = [sys.executable, "-m", "py_compile", *[str(p) for p in files]]
    proc = subprocess.run(cmd, cwd=str(stage), text=True, capture_output=True, timeout=120)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "py_compile a échoué")[-5000:]
    return True, "Compilation Python OK"


def _snapshot_hashes(folder: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p, rel in _iter_allowed_files(folder):
        try:
            out[str(rel)] = p.read_bytes()
        except OSError:
            pass
    return out


def _changed_between(before: dict[str, bytes], stage: Path) -> list[str]:
    after = _snapshot_hashes(stage)
    keys = sorted(set(before) | set(after))
    return [k for k in keys if before.get(k) != after.get(k)]


def _run_codex(stage: Path, prompt: str, images: list[Path], log=None) -> str:
    if not codex_available():
        raise RuntimeError("Codex CLI n'est pas détecté. Utilise CODEX_LOGIN.bat puis relance AutoMix.")

    image_args: list[str] = []
    for img in images:
        if img.exists():
            image_args += ["-i", str(img)]

    attempts = [
        ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write", "-a", "never", *image_args, "-"],
        ["codex", "exec", "--skip-git-repo-check", "--full-auto", *image_args, "-"],
        ["codex", "--full-auto", *image_args, "exec", "--skip-git-repo-check", "-"],
    ]
    last = ""
    for i, cmd in enumerate(attempts, 1):
        _emit(log, f"Auto-réparation Codex : tentative {i}/{len(attempts)}…")
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(stage),
                timeout=900,
            )
        except Exception as exc:
            last = str(exc)
            continue
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
        last = (proc.stderr or proc.stdout or f"code {proc.returncode}").strip()
        # Don't retry the same task if the model itself failed for a non-CLI reason.
        low = last.lower()
        if not any(k in low for k in ("unknown", "unexpected", "unrecognized", "invalid value", "found argument")):
            break
    raise RuntimeError("Codex n'a pas pu lancer l'auto-réparation. Dernière erreur : " + last[-3000:])


def run_self_repair(
    root: str | Path,
    *,
    workspace: str | Path | None,
    user_text: str,
    screenshots: list[str | Path],
    runtime_log: str,
    log: Callable[[str], None] | None = None,
) -> RepairResult:
    """Repair AutoMix in an isolated staging copy, then atomically copy code back.

    Codex never receives the user's .als project as a writable working tree.  It
    only sees a staging copy of AutoMix source plus diagnostic text/screenshots.
    """
    root = Path(root).resolve()
    workspace_p = Path(workspace).resolve() if workspace else None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    repair_root = root / ".self_repair"
    stage = repair_root / f"stage_{stamp}"
    backup = repair_root / f"backup_{stamp}"
    context = stage / "context"
    context.mkdir(parents=True, exist_ok=True)

    _emit(log, "Je crée une copie isolée du code avant de demander le correctif à Codex…")
    _copy_allowed_tree(root, stage)
    before_stage = _snapshot_hashes(stage)

    feedback_path = context / "feedback.txt"
    feedback_path.write_text(user_text.strip() or "Aucun commentaire texte supplémentaire.", encoding="utf-8")
    (context / "runtime_log.txt").write_text(runtime_log[-40000:], encoding="utf-8")

    diag = _latest_diagnostic(workspace_p)
    if diag and diag.exists():
        shutil.copy2(diag, context / "ableton_export_dialog.txt")

    attached: list[Path] = []
    for idx, raw in enumerate(screenshots, 1):
        src = Path(raw)
        if not src.exists() or not src.is_file():
            continue
        suffix = src.suffix.lower() if src.suffix else ".png"
        dst = context / f"screenshot_{idx:02d}{suffix}"
        shutil.copy2(src, dst)
        attached.append(dst)

    prompt = f"""
Tu répares une application locale Windows appelée Ableton AutoMix V11.
Tu travailles DANS UNE COPIE ISOLÉE du code : applique réellement les corrections nécessaires aux fichiers présents dans ce dossier.

OBJECTIF
- Comprendre l'erreur décrite dans context/feedback.txt et context/runtime_log.txt.
- Lire context/ableton_export_dialog.txt s'il existe.
- Examiner attentivement les captures jointes à ce prompt s'il y en a.
- Corriger l'automatisation Ableton et/ou l'interface AutoMix de façon minimale et robuste.
- Ne te contente PAS d'expliquer : modifie le code.

CONTRAINTES IMPÉRATIVES
- Ne crée pas de clé API et ne demande pas de secret.
- Ne touche jamais à un projet .als utilisateur : aucun .als n'est présent dans ce staging et il ne faut pas en créer.
- Ne supprime pas les fonctions de sécurité qui gardent l'original intact.
- Préfère les contrôles d'accessibilité ; si Ableton dessine un contrôle custom, un fallback par coordonnées NORMALISÉES de la fenêtre est acceptable.
- Les erreurs d'interface doivent pouvoir être renvoyées vers l'onglet Correction / aide.
- Garde l'application simple : l'utilisateur doit essentiellement cliquer sur les gros boutons.
- Après tes changements, exécute une validation Python (py_compile au minimum) et corrige toute erreur rencontrée.
- N'installe rien globalement et ne lance aucun programme externe autre que les tests locaux nécessaires.

FEEDBACK UTILISATEUR
{user_text.strip() or '(voir les fichiers context/)'}

À la fin, donne un résumé court des fichiers modifiés et de ce que tu as corrigé.
""".strip()

    output = _run_codex(stage, prompt, attached, log=log)
    ok, validation = _validate_python(stage)
    _emit(log, validation)
    if not ok:
        raise RuntimeError("Le correctif Codex a été refusé car le code ne compile plus :\n" + validation)

    changed = _changed_between(before_stage, stage)
    if not changed:
        return RepairResult(False, output or "Codex n'a modifié aucun fichier.", [], None, str(stage))

    # Backup only the application source, never user projects/workspaces.
    _copy_allowed_tree(root, backup)
    for src, rel in _iter_allowed_files(stage):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    result_payload = {
        "timestamp": stamp,
        "changed_files": changed,
        "validation": validation,
        "codex_summary": output[-12000:],
        "backup_dir": str(backup),
        "stage_dir": str(stage),
    }
    (repair_root / "last_repair.json").write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _emit(log, "Correctif appliqué au logiciel. Une sauvegarde du code précédent a été créée.")
    return RepairResult(True, output or "Correctif appliqué.", changed, str(backup), str(stage))


def restore_latest_backup(root: str | Path, log=None) -> str:
    root = Path(root).resolve()
    repair_root = root / ".self_repair"
    backups = sorted([p for p in repair_root.glob("backup_*") if p.is_dir()], reverse=True)
    if not backups:
        raise RuntimeError("Aucune sauvegarde d'auto-réparation n'est disponible.")
    backup = backups[0]
    _emit(log, f"Restauration de {backup.name}…")
    for src, rel in _iter_allowed_files(backup):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    ok, validation = _validate_python(root)
    if not ok:
        raise RuntimeError("La sauvegarde restaurée ne compile pas : " + validation)
    return str(backup)
