from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PRESERVE_TOP_LEVEL = {
    "AutoMix_Projects",
    "workspaces",
    ".last_project.txt",
    "update_config.json",
    ".feedback",
    ".repair_backups",
}


def wait_for_pid(pid: int, timeout: float = 60.0) -> None:
    try:
        import psutil
    except Exception:
        time.sleep(2.0)
        return
    end = time.time() + timeout
    while time.time() < end:
        if not psutil.pid_exists(pid):
            return
        time.sleep(0.4)


def payload_root(extracted: Path) -> Path:
    children = [p for p in extracted.iterdir() if p.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "automix_app.py").exists():
        return children[0]
    if (extracted / "automix_app.py").exists():
        return extracted
    for p in extracted.rglob("automix_app.py"):
        return p.parent
    raise RuntimeError("Le ZIP de mise à jour ne contient pas automix_app.py.")


def apply_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name in PRESERVE_TOP_LEVEL or item.name == "__pycache__":
            continue
        target = dst / item.name
        if item.is_dir():
            if target.exists() and target.is_file():
                target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            apply_tree(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--pid", required=True, type=int)
    args = ap.parse_args()
    archive = Path(args.archive).resolve()
    root = Path(args.root).resolve()
    wait_for_pid(args.pid)
    with tempfile.TemporaryDirectory(prefix="automix_update_") as td:
        extracted = Path(td)
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(extracted)
        src = payload_root(extracted)
        apply_tree(src, root)
    try:
        archive.unlink(missing_ok=True)
    except Exception:
        pass
    subprocess.Popen([sys.executable, str(root / "automix_app.py")], cwd=str(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
