from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass
class UpdateInfo:
    version: int
    url: str = ""
    sha256: str = ""
    notes: str = ""
    files: list[dict] = field(default_factory=list)


def _version_int(value) -> int:
    text = str(value).strip().lower().lstrip("v")
    return int(text.split(".")[0])


def load_update_config(root: Path) -> dict:
    cfg = root / "update_config.json"
    if not cfg.exists():
        return {}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def manifest_url(root: Path) -> str:
    env = os.environ.get("AUTOMIX_UPDATE_MANIFEST", "").strip()
    if env:
        return env
    return str(load_update_config(root).get("manifest_url", "")).strip()


def check_for_update(root: Path, current_version: int, timeout: float = 10.0) -> UpdateInfo | None:
    url = manifest_url(root)
    if not url:
        raise RuntimeError(
            "Le moteur de mise à jour est installé, mais son adresse de publication n'est pas encore configurée."
        )
    payload = json.loads(_download_bytes(url, timeout).decode("utf-8"))

    raw_files = payload.get("files", [])
    files = raw_files if isinstance(raw_files, list) else []
    info = UpdateInfo(
        version=_version_int(payload["version"]),
        url=str(payload.get("url", "")).strip(),
        sha256=str(payload.get("sha256", "")).strip().lower(),
        notes=str(payload.get("notes", "")).strip(),
        files=files,
    )
    return info if info.version > int(current_version) else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_rel_path(value: str) -> Path:
    posix = PurePosixPath(str(value).replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise RuntimeError(f"Chemin de mise à jour refusé : {value}")
    return Path(*posix.parts)


def _download_bytes(url: str, timeout: float, attempts: int = 4) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Ableton-AutoMix-Updater/13",
                "Accept": "*/*",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(min(4.0, 0.75 * attempt))
    raise RuntimeError(
        f"Téléchargement interrompu après {attempts} tentative(s) : {last_exc}"
    ) from last_exc


def _build_file_update_zip(info: UpdateInfo, timeout: float) -> Path:
    """Télécharge une mise à jour fichier-par-fichier depuis GitHub.

    Ce mode évite d'avoir à publier un ZIP binaire à chaque version. Le manifeste
    peut simplement pointer vers les fichiers texte modifiés dans le dépôt GitHub.
    AutoMix reconstruit ensuite localement un petit ZIP que le worker existant sait
    déjà appliquer.
    """
    if not info.files:
        raise RuntimeError("Le manifeste de mise à jour ne contient aucun fichier à installer.")

    staging = Path(tempfile.mkdtemp(prefix=f"automix_v{info.version}_files_"))
    try:
        saw_main = False
        for entry in info.files:
            if not isinstance(entry, dict):
                raise RuntimeError("Entrée de fichier invalide dans le manifeste de mise à jour.")
            rel = _safe_rel_path(str(entry.get("path", "")))
            url = str(entry.get("url", "")).strip()
            expected = str(entry.get("sha256", "")).strip().lower()
            if not url:
                raise RuntimeError(f"URL manquante pour {rel.as_posix()}.")
            data = _download_bytes(url, timeout)
            if expected and _sha256_bytes(data).lower() != expected:
                raise RuntimeError(f"Contrôle SHA-256 échoué pour {rel.as_posix()}.")
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if rel.as_posix() == "automix_app.py":
                saw_main = True

        # Le worker se sert d'automix_app.py comme ancre pour localiser la racine.
        if not saw_main:
            raise RuntimeError("La mise à jour doit inclure automix_app.py.")

        target_zip = Path(tempfile.gettempdir()) / f"Ableton_AutoMix_V{info.version}_update.zip"
        target_zip.unlink(missing_ok=True)
        with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in staging.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(staging).as_posix())
        return target_zip
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def download_update(info: UpdateInfo, timeout: float = 60.0) -> Path:
    # Nouveau canal V11 : le manifeste peut lister directement les fichiers
    # publiés sur GitHub. Le mode ZIP historique reste compatible en secours.
    if info.files:
        return _build_file_update_zip(info, timeout)

    if not info.url:
        raise RuntimeError("Le manifeste de mise à jour ne contient ni fichiers ni URL de ZIP.")

    target = Path(tempfile.gettempdir()) / f"Ableton_AutoMix_V{info.version}_update.zip"
    part = target.with_suffix(".zip.part")
    target.unlink(missing_ok=True)
    part.unlink(missing_ok=True)
    last_exc: Exception | None = None
    for attempt in range(1, 5):
        try:
            data = _download_bytes(info.url, timeout, attempts=1)
            part.write_bytes(data)
            part.replace(target)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            part.unlink(missing_ok=True)
            if attempt < 4:
                time.sleep(min(4.0, 0.75 * attempt))
    if last_exc is not None:
        raise RuntimeError(
            f"Impossible de télécharger la mise à jour après 4 tentatives : {last_exc}"
        ) from last_exc
    if info.sha256:
        actual = _sha256(target)
        if actual.lower() != info.sha256.lower():
            target.unlink(missing_ok=True)
            raise RuntimeError("La mise à jour téléchargée a échoué au contrôle SHA-256.")
    if not zipfile.is_zipfile(target):
        target.unlink(missing_ok=True)
        raise RuntimeError("Le fichier de mise à jour téléchargé n'est pas un ZIP valide.")
    return target


def launch_update_worker(root: Path, archive: Path) -> None:
    worker = root / "update_worker.py"
    if not worker.exists():
        raise RuntimeError("update_worker.py est introuvable.")
    subprocess.Popen(
        [sys.executable, str(worker), "--archive", str(archive), "--root", str(root), "--pid", str(os.getpid())],
        cwd=str(root),
        close_fds=True,
    )
