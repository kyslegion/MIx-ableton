from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from .als import ProjectInfo
from .audio import AudioMetrics, normalize_name


@dataclass
class MixAction:
    track_id: str
    track_name: str
    stem_file: str | None
    gain_db_delta: float
    pan: float | None
    reason: str
    confidence: float

    def to_dict(self):
        return asdict(self)


def _similarity(track: str, filename: str) -> float:
    a = normalize_name(track)
    b = normalize_name(Path(filename).stem)
    direct = SequenceMatcher(None, a, b).ratio()
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    token = len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))
    return max(direct, token)


def match_stems(project: ProjectInfo, metrics: Iterable[AudioMetrics]) -> dict[str, AudioMetrics]:
    metrics = list(metrics)
    used: set[int] = set()
    out: dict[str, AudioMetrics] = {}
    for track in project.tracks:
        if not getattr(track, "mixable", True):
            continue
        scored = [(i, _similarity(track.name, m.file)) for i, m in enumerate(metrics) if i not in used]
        if not scored:
            continue
        i, score = max(scored, key=lambda x: x[1])
        if score >= 0.28:
            out[track.track_id] = metrics[i]
            used.add(i)
    return out


def _role(name: str) -> str:
    n = normalize_name(name)
    if any(x in n for x in ["vocal", "voco", "voice", "voix", "chant"]):
        return "vocal"
    if any(x in n for x in [
        "kick", "snare", "drum", "perc", "batterie", "cymbal", "cymbale",
        "hihat", "hi hat", "hat", "clap", "tom", "shaker", "ride", "crash",
    ]):
        return "drums"
    if any(x in n for x in ["bass", "basse", "sub"]):
        return "bass"
    if any(x in n for x in ["guitar", "guitare"]):
        return "guitar"
    if any(x in n for x in ["organ", "orgue", "pad", "strings", "string"]):
        return "pad"
    if any(x in n for x in ["marimba", "piano", "keys", "key"]):
        return "keys"
    return "other"


ROLE_OFFSET = {
    "vocal": 1.5,
    "drums": 1.0,
    "bass": 0.8,
    "guitar": -0.3,
    "pad": -1.5,
    "keys": -0.8,
    "other": 0.0,
}


def propose_local_mix(project: ProjectInfo, metrics: Iterable[AudioMetrics]) -> list[MixAction]:
    metrics = list(metrics)
    matches = match_stems(project, metrics)
    rms_values = [m.rms_dbfs for m in matches.values() if math.isfinite(m.rms_dbfs)]
    if not rms_values:
        return []
    rms_values.sort()
    median = rms_values[len(rms_values) // 2]

    roles: dict[str, list] = {}
    for t in project.tracks:
        if getattr(t, "mixable", True):
            roles.setdefault(_role(t.name), []).append(t)

    actions: list[MixAction] = []
    for track in project.tracks:
        m = matches.get(track.track_id)
        if m is None:
            continue
        role = _role(track.name)
        target = median + ROLE_OFFSET.get(role, 0.0)
        delta = target - m.rms_dbfs
        # First-pass gain moves should be conservative.
        delta = max(-4.0, min(4.0, delta))
        if abs(delta) < 0.25:
            delta = 0.0

        pan = None
        # Only mild suggestions; duplicated tonal parts can be separated.
        siblings = roles.get(role, [])
        if role in {"pad", "keys", "guitar", "other"} and len(siblings) >= 2:
            idx = siblings.index(track)
            pan = -0.16 if idx % 2 == 0 else 0.16
        elif role == "guitar":
            pan = 0.10
        elif role == "keys":
            pan = -0.08

        reason_parts = [f"RMS {m.rms_dbfs:.1f} dBFS; cible de première passe {target:.1f} dBFS ({role})."]
        if m.peak_dbfs > -1.0:
            reason_parts.append("Crête très proche de 0 dBFS : marge réduite.")
        if m.stereo_correlation is not None and m.stereo_correlation < -0.1:
            reason_parts.append("Corrélation stéréo négative : vérifier la phase/mono.")
        actions.append(
            MixAction(
                track_id=track.track_id,
                track_name=track.name,
                stem_file=m.file,
                gain_db_delta=round(delta, 2),
                pan=pan,
                reason=" ".join(reason_parts),
                confidence=0.72,
            )
        )
    return actions


def build_ai_payload(project: ProjectInfo, metrics: Iterable[AudioMetrics]) -> dict:
    matches = match_stems(project, metrics)
    return {
        "project": project.to_dict(),
        "stem_metrics_by_track_id": {k: v.to_dict() for k, v in matches.items()},
        "constraints": {
            "gain_delta_db_min": -4.0,
            "gain_delta_db_max": 4.0,
            "pan_min": -0.35,
            "pan_max": 0.35,
            "first_pass_only": True,
            "never_change_original_file": True,
        },
    }


def save_actions(path: str | Path, actions: Iterable[MixAction | dict]):
    serial = [a.to_dict() if hasattr(a, "to_dict") else a for a in actions]
    Path(path).write_text(json.dumps(serial, ensure_ascii=False, indent=2), encoding="utf-8")
