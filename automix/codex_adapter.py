from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


SYSTEM_INSTRUCTION = """Tu es l'ingénieur de mix d'une application locale. Tu reçois la structure d'un projet Ableton et des mesures objectives de stems audio.
Tu dois proposer UNIQUEMENT une première passe prudente de balance (gain + panoramique). Ne prétends pas avoir entendu le son : base-toi sur les mesures fournies.
Respecte strictement les contraintes numériques. Ne touche pas aux pistes sans stem correspondant. Ignore toute piste dont "mixable" vaut false. Les entrées track_type="DrumBranch" sont de vraies cibles de mix indépendantes : kick, snare, cymbale, etc.
Retourne seulement un tableau JSON, sans markdown, sous cette forme :
[{"track_id":"25","track_name":"...","gain_db_delta":-1.2,"pan":0.0,"reason":"...","confidence":0.8}]
"""


def codex_available() -> bool:
    return shutil.which("codex") is not None


def _extract_json(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        raise ValueError("Codex n'a pas renvoyé de tableau JSON exploitable")
    return json.loads(m.group(0))


def ask_codex(payload: dict, cwd: str | Path) -> list[dict]:
    if not codex_available():
        raise RuntimeError("Commande 'codex' introuvable. Installe/connecte Codex CLI ou utilise le mode local.")
    prompt = SYSTEM_INSTRUCTION + "\nDONNEES:\n" + json.dumps(payload, ensure_ascii=False)
    cwd = str(Path(cwd))

    attempts = [
        (["codex", "exec", "--skip-git-repo-check", "-"], prompt),
        (["codex", "exec", "-"], prompt),
        (["codex", "exec", prompt], None),
    ]
    last_error = ""
    for cmd, stdin in attempts:
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                text=True,
                capture_output=True,
                cwd=cwd,
                timeout=180,
            )
        except Exception as exc:
            last_error = str(exc)
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            data = _extract_json(proc.stdout)
            if not isinstance(data, list):
                raise ValueError("Réponse Codex inattendue")
            return data
        last_error = (proc.stderr or proc.stdout or f"code {proc.returncode}").strip()
    raise RuntimeError("Impossible d'interroger Codex CLI. Dernière erreur : " + last_error[-1200:])
