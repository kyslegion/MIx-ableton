from __future__ import annotations

import gzip
import math
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

MAX_AMP = 1.99526238  # +6 dB in Ableton's mixer scale
MIN_AMP = 1e-8


def amp_to_db(value: float) -> float:
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(value)


def db_to_amp(db: float) -> float:
    return 10.0 ** (db / 20.0)


@dataclass
class TrackInfo:
    track_id: str
    track_type: str
    name: str
    volume_amp: float
    volume_db: float
    pan: float
    devices: list[str]

    def to_dict(self):
        return asdict(self)


@dataclass
class ProjectInfo:
    path: str
    creator: str
    major_version: str
    minor_version: str
    tracks: list[TrackInfo]
    returns: int

    def to_dict(self):
        return {
            "path": self.path,
            "creator": self.creator,
            "major_version": self.major_version,
            "minor_version": self.minor_version,
            "returns": self.returns,
            "tracks": [t.to_dict() for t in self.tracks],
        }


def _load_xml(path: Path) -> tuple[ET.Element, bytes]:
    raw = path.read_bytes()
    try:
        xml_bytes = gzip.decompress(raw)
    except OSError as exc:
        raise ValueError(f"Le fichier ne ressemble pas à un .als gzip valide: {exc}") from exc
    try:
        return ET.fromstring(xml_bytes), xml_bytes
    except ET.ParseError as exc:
        raise ValueError(f"XML Ableton illisible: {exc}") from exc


def _effective_name(track: ET.Element) -> str:
    for rel in ("./Name/EffectiveName", "./Name/UserName"):
        node = track.find(rel)
        if node is not None and node.attrib.get("Value"):
            return node.attrib["Value"]
    return f"Track {track.attrib.get('Id', '?')}"


def _top_mixer(track: ET.Element) -> ET.Element | None:
    # Mixer at the track DeviceChain level, not a rack's internal mixer.
    mixer = track.find("./DeviceChain/Mixer")
    if mixer is not None:
        return mixer
    return track.find(".//Mixer")


def _devices(track: ET.Element) -> list[str]:
    devices_node = track.find("./DeviceChain/DeviceChain/Devices")
    if devices_node is None:
        devices_node = track.find("./DeviceChain/Devices")
    if devices_node is None:
        devices_node = track.find(".//DeviceChain/Devices")
    if devices_node is None:
        return []
    return [d.tag for d in list(devices_node)]


def parse_als(path: str | Path) -> ProjectInfo:
    path = Path(path)
    root, _ = _load_xml(path)
    tracks_parent = root.find("./LiveSet/Tracks")
    tracks: list[TrackInfo] = []
    returns = 0
    if tracks_parent is not None:
        for track in list(tracks_parent):
            if track.tag == "ReturnTrack":
                returns += 1
                continue
            if track.tag not in {"MidiTrack", "AudioTrack", "GroupTrack"}:
                continue
            mixer = _top_mixer(track)
            vol = 1.0
            pan = 0.0
            if mixer is not None:
                n = mixer.find("./Volume/Manual")
                if n is not None:
                    try:
                        vol = float(n.attrib.get("Value", "1"))
                    except ValueError:
                        vol = 1.0
                n = mixer.find("./Pan/Manual")
                if n is not None:
                    try:
                        pan = float(n.attrib.get("Value", "0"))
                    except ValueError:
                        pan = 0.0
            tracks.append(
                TrackInfo(
                    track_id=track.attrib.get("Id", ""),
                    track_type=track.tag,
                    name=_effective_name(track),
                    volume_amp=vol,
                    volume_db=amp_to_db(vol),
                    pan=pan,
                    devices=_devices(track),
                )
            )
    return ProjectInfo(
        path=str(path),
        creator=root.attrib.get("Creator", ""),
        major_version=root.attrib.get("MajorVersion", ""),
        minor_version=root.attrib.get("MinorVersion", ""),
        tracks=tracks,
        returns=returns,
    )


def apply_mix_copy(
    source: str | Path,
    output: str | Path,
    actions: Iterable[dict],
) -> Path:
    """Apply gain deltas/pan to a COPY of an ALS.

    actions entries: {track_id or track_name, gain_db_delta?, pan?}
    """
    source = Path(source)
    output = Path(output)
    root, _ = _load_xml(source)
    actions = list(actions)
    by_id = {str(a.get("track_id")): a for a in actions if a.get("track_id") is not None}
    by_name = {str(a.get("track_name")): a for a in actions if a.get("track_name")}

    tracks_parent = root.find("./LiveSet/Tracks")
    if tracks_parent is None:
        raise ValueError("Aucune section Tracks trouvée")

    changed = 0
    for track in list(tracks_parent):
        if track.tag not in {"MidiTrack", "AudioTrack", "GroupTrack"}:
            continue
        tid = track.attrib.get("Id", "")
        name = _effective_name(track)
        action = by_id.get(tid) or by_name.get(name)
        if not action:
            continue
        mixer = _top_mixer(track)
        if mixer is None:
            continue
        if action.get("gain_db_delta") is not None:
            node = mixer.find("./Volume/Manual")
            if node is not None:
                current = float(node.attrib.get("Value", "1"))
                new_val = current * db_to_amp(float(action["gain_db_delta"]))
                new_val = min(MAX_AMP, max(0.0, new_val))
                node.attrib["Value"] = f"{new_val:.10f}".rstrip("0").rstrip(".")
                changed += 1
        if action.get("pan") is not None:
            node = mixer.find("./Pan/Manual")
            if node is not None:
                pan = min(1.0, max(-1.0, float(action["pan"])))
                node.attrib["Value"] = f"{pan:.6f}".rstrip("0").rstrip(".")
                changed += 1

    if changed == 0:
        raise ValueError("Aucun réglage n'a pu être appliqué")

    output.parent.mkdir(parents=True, exist_ok=True)
    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with output.open("wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(xml_out)

    # Safety validation before returning the file.
    parse_als(output)
    return output


def make_backup(path: str | Path) -> Path:
    path = Path(path)
    backup = path.with_suffix(path.suffix + ".backup")
    shutil.copy2(path, backup)
    return backup


def estimate_arrangement_end_beats(path: str | Path, fallback: float = 128.0) -> float:
    """Best-effort end-of-song estimate for Arrangement export.

    Ableton stores arrangement clips with CurrentEnd values in beats. We only
    accept sane values to avoid confusing MIDI note tick counters with song time.
    """
    root, _ = _load_xml(Path(path))
    vals: list[float] = []
    for node in root.iter("CurrentEnd"):
        raw = node.attrib.get("Value")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if 0.0 < v < 10000.0:
            vals.append(v)
    if vals:
        return max(vals)
    transport = root.find("./LiveSet/Transport/CurrentTime")
    if transport is not None:
        try:
            v = float(transport.attrib.get("Value", fallback))
            if 0.0 < v < 10000.0:
                return max(v, 16.0)
        except ValueError:
            pass
    return fallback


def make_render_source_copy(
    source: str | Path,
    output: str | Path,
    *,
    tail_beats: float = 4.0,
) -> tuple[Path, float]:
    """Create a disposable ALS copy with a full-song time selection.

    This copy is used only to make Live's Export dialog inherit a deterministic
    render range. The user's source set is never edited.
    """
    source = Path(source)
    output = Path(output)
    root, _ = _load_xml(source)
    end = estimate_arrangement_end_beats(source)
    end = max(4.0, end + max(0.0, float(tail_beats)))

    live_set = root.find("./LiveSet")
    if live_set is None:
        raise ValueError("Section LiveSet introuvable")
    sel = live_set.find("./TimeSelection")
    if sel is None:
        sel = ET.SubElement(live_set, "TimeSelection")
    anchor = sel.find("./AnchorTime")
    if anchor is None:
        anchor = ET.SubElement(sel, "AnchorTime")
    other = sel.find("./OtherTime")
    if other is None:
        other = ET.SubElement(sel, "OtherTime")
    anchor.attrib["Value"] = "0"
    other.attrib["Value"] = f"{end:.6f}".rstrip("0").rstrip(".")

    output.parent.mkdir(parents=True, exist_ok=True)
    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with output.open("wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(xml_out)
    parse_als(output)
    return output, end
