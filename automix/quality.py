from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from statistics import median
from typing import Iterable

from .als import ProjectInfo
from .audio import AudioMetrics
from .mix_engine import match_stems, _role, ROLE_OFFSET


@dataclass
class QualityReport:
    score: float
    matched_tracks: int
    mean_balance_error_db: float
    clipping_risk_tracks: int
    negative_phase_tracks: int
    stable: bool
    summary: str

    def to_dict(self):
        return asdict(self)


def evaluate_balance(project: ProjectInfo, metrics: Iterable[AudioMetrics]) -> QualityReport:
    metrics = list(metrics)
    matches = match_stems(project, metrics)
    if not matches:
        return QualityReport(0.0, 0, 99.0, 0, 0, False, "Aucun stem n'a pu être associé aux pistes.")

    rms_values = [m.rms_dbfs for m in matches.values() if math.isfinite(m.rms_dbfs)]
    if not rms_values:
        return QualityReport(0.0, len(matches), 99.0, 0, 0, False, "Mesures RMS inutilisables.")
    med = median(rms_values)

    errors = []
    clipping = 0
    phase = 0
    for t in project.tracks:
        m = matches.get(t.track_id)
        if not m:
            continue
        target = med + ROLE_OFFSET.get(_role(t.name), 0.0)
        errors.append(abs(m.rms_dbfs - target))
        if m.peak_dbfs > -0.5:
            clipping += 1
        if m.stereo_correlation is not None and m.stereo_correlation < -0.15:
            phase += 1

    err = sum(errors) / max(1, len(errors))
    score = 100.0 - err * 11.0 - clipping * 7.0 - phase * 4.0
    score = max(0.0, min(100.0, score))
    stable = err <= 0.45 and clipping == 0
    summary = (
        f"équilibre moyen à {err:.2f} dB de la cible; "
        f"{clipping} piste(s) proche(s) de 0 dBFS; {phase} alerte(s) de phase"
    )
    return QualityReport(round(score, 1), len(matches), round(err, 2), clipping, phase, stable, summary)
