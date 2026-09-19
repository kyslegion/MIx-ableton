from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    import pyloudnorm as pyln
except Exception:
    pyln = None

AUDIO_EXTS = {".wav", ".wave", ".aif", ".aiff", ".flac"}


def db(x: float, floor: float = -120.0) -> float:
    if x <= 0:
        return floor
    return 20.0 * math.log10(x)


@dataclass
class AudioMetrics:
    file: str
    sample_rate: int
    channels: int
    duration_s: float
    peak_dbfs: float
    rms_dbfs: float
    lufs_i: float | None
    crest_db: float
    spectral_centroid_hz: float
    low_ratio: float
    mid_ratio: float
    high_ratio: float
    stereo_correlation: float | None

    def to_dict(self):
        return asdict(self)


def _read_audio(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True, dtype="float32")
    if data.size == 0:
        raise ValueError("fichier audio vide")
    return data, int(sr)


def _mono(data: np.ndarray) -> np.ndarray:
    return data.mean(axis=1)


def _spectrum_features(mono: np.ndarray, sr: int) -> tuple[float, float, float, float]:
    # Downsample long signals for fast analysis while preserving broad balance.
    max_samples = sr * 180
    if len(mono) > max_samples:
        step = max(1, len(mono) // max_samples)
        mono = mono[::step]
        effective_sr = sr / step
    else:
        effective_sr = sr
    if len(mono) < 2048:
        padded = np.zeros(2048, dtype=np.float32)
        padded[: len(mono)] = mono
        mono = padded
    window = np.hanning(len(mono))
    spec = np.abs(np.fft.rfft(mono * window)) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1.0 / effective_sr)
    total = float(spec.sum()) + 1e-20
    centroid = float((freqs * spec).sum() / total)

    def ratio(lo, hi):
        mask = (freqs >= lo) & (freqs < hi)
        return float(spec[mask].sum() / total)

    return centroid, ratio(20, 250), ratio(250, 4000), ratio(4000, min(20000, effective_sr / 2))


def analyze_file(path: str | Path) -> AudioMetrics:
    path = Path(path)
    data, sr = _read_audio(path)
    mono = _mono(data)
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
    peak_db = db(peak)
    rms_db = db(rms)
    crest = peak_db - rms_db
    lufs = None
    if pyln is not None and len(mono) >= int(sr * 0.5):
        try:
            meter = pyln.Meter(sr)
            lufs = float(meter.integrated_loudness(mono.astype(np.float64)))
            if not np.isfinite(lufs):
                lufs = None
        except Exception:
            lufs = None
    centroid, low, mid, high = _spectrum_features(mono, sr)
    corr = None
    if data.shape[1] >= 2:
        left = data[:, 0].astype(np.float64)
        right = data[:, 1].astype(np.float64)
        if np.std(left) > 1e-9 and np.std(right) > 1e-9:
            corr = float(np.corrcoef(left, right)[0, 1])
    return AudioMetrics(
        file=str(path),
        sample_rate=sr,
        channels=int(data.shape[1]),
        duration_s=float(len(data) / sr),
        peak_dbfs=peak_db,
        rms_dbfs=rms_db,
        lufs_i=lufs,
        crest_db=crest,
        spectral_centroid_hz=centroid,
        low_ratio=low,
        mid_ratio=mid,
        high_ratio=high,
        stereo_correlation=corr,
    )


def scan_folder(folder: str | Path) -> list[AudioMetrics]:
    folder = Path(folder)
    paths = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    return [analyze_file(p) for p in sorted(paths)]


def normalize_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9à-ÿ]+", " ", s)
    return " ".join(s.split())
