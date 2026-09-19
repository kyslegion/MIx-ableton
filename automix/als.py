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
    target_kind: str = "track"
    mixable: bool = True
    physical_track_index: int | None = None
    parent_track_id: str | None = None
    parent_track_name: str | None = None
    drum_device_id: str | None = None
    drum_branch_id: str | None = None

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
    physical_tracks: int = 0
    drum_branches: int = 0

    def to_dict(self):
        return {
            "path": self.path,
            "creator": self.creator,
            "major_version": self.major_version,
            "minor_version": self.minor_version,
            "returns": self.returns,
            "physical_tracks": self.physical_tracks,
            "drum_branches": self.drum_branches,
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



def _top_devices_node(track: ET.Element) -> ET.Element | None:
    node = track.find("./DeviceChain/DeviceChain/Devices")
    if node is None:
        node = track.find("./DeviceChain/Devices")
    return node


def _direct_drum_devices(track: ET.Element) -> list[ET.Element]:
    node = _top_devices_node(track)
    if node is None:
        return []
    return [d for d in list(node) if d.tag == "DrumGroupDevice"]


def _drum_branch_name(branch: ET.Element) -> str:
    for rel in ("./Name/EffectiveName", "./Name/UserName", "./Name"):
        n = branch.find(rel)
        if n is not None and n.attrib.get("Value"):
            return n.attrib["Value"]
    return f"Drum {branch.attrib.get('Id', '?')}"


def _drum_branch_devices(branch: ET.Element) -> list[str]:
    node = branch.find("./DeviceChain/MidiToAudioDeviceChain/Devices")
    if node is None:
        node = branch.find(".//MidiToAudioDeviceChain/Devices")
    if node is None:
        return []
    return [d.tag for d in list(node)]


def _drum_branch_mixer(branch: ET.Element) -> ET.Element | None:
    return branch.find("./MixerDevice")


def _read_manual(parent: ET.Element | None, path: str, default: float) -> float:
    if parent is None:
        return default
    n = parent.find(path)
    if n is None:
        return default
    try:
        return float(n.attrib.get("Value", str(default)))
    except ValueError:
        return default


def _synthetic_drum_id(track_id: str, device_id: str, branch_id: str) -> str:
    return f"drum:{track_id}:{device_id}:{branch_id}"


def parse_als(path: str | Path) -> ProjectInfo:
    path = Path(path)
    root, _ = _load_xml(path)
    tracks_parent = root.find("./LiveSet/Tracks")
    tracks: list[TrackInfo] = []
    returns = 0
    physical_index = 0
    drum_branch_count = 0

    if tracks_parent is not None:
        for track in list(tracks_parent):
            if track.tag == "ReturnTrack":
                returns += 1
                continue
            if track.tag not in {"MidiTrack", "AudioTrack", "GroupTrack"}:
                continue

            tid = track.attrib.get("Id", "")
            parent_name = _effective_name(track)
            mixer = _top_mixer(track)
            vol = _read_manual(mixer, "./Volume/Manual", 1.0)
            pan = _read_manual(mixer, "./Pan/Manual", 0.0)

            branch_rows: list[TrackInfo] = []
            for drum in _direct_drum_devices(track):
                did = drum.attrib.get("Id", "")
                branches = drum.find("./Branches")
                if branches is None:
                    continue
                for branch in list(branches):
                    if branch.tag != "DrumBranch":
                        continue
                    devices = _drum_branch_devices(branch)
                    if not devices:
                        continue
                    bid = branch.attrib.get("Id", "")
                    branch_name = _drum_branch_name(branch)
                    bm = _drum_branch_mixer(branch)
                    bvol = _read_manual(bm, "./Volume/Manual", 1.0)
                    bpan = _read_manual(bm, "./Panorama/Manual", 0.0)
                    branch_rows.append(
                        TrackInfo(
                            track_id=_synthetic_drum_id(tid, did, bid),
                            track_type="DrumBranch",
                            name=f"{parent_name} / {branch_name}",
                            volume_amp=bvol,
                            volume_db=amp_to_db(bvol),
                            pan=bpan,
                            devices=devices,
                            target_kind="drum_branch",
                            mixable=True,
                            physical_track_index=physical_index,
                            parent_track_id=tid,
                            parent_track_name=parent_name,
                            drum_device_id=did,
                            drum_branch_id=bid,
                        )
                    )

            tracks.append(
                TrackInfo(
                    track_id=tid,
                    track_type=track.tag,
                    name=parent_name,
                    volume_amp=vol,
                    volume_db=amp_to_db(vol),
                    pan=pan,
                    devices=_devices(track),
                    target_kind="track",
                    mixable=not bool(branch_rows),
                    physical_track_index=physical_index,
                )
            )
            tracks.extend(branch_rows)
            drum_branch_count += len(branch_rows)
            physical_index += 1

    return ProjectInfo(
        path=str(path),
        creator=root.attrib.get("Creator", ""),
        major_version=root.attrib.get("MajorVersion", ""),
        minor_version=root.attrib.get("MinorVersion", ""),
        tracks=tracks,
        returns=returns,
        physical_tracks=physical_index,
        drum_branches=drum_branch_count,
    )


def apply_mix_copy(
    source: str | Path,
    output: str | Path,
    actions: Iterable[dict],
) -> Path:
    """Apply gain/pan decisions to a COPY of an ALS.

    Drum Rack sub-instruments use synthetic ids and are changed directly in
    their DrumBranch MixerDevice, so kick/snare/cymbal can be mixed separately.
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

    def apply_to_mixer(mixer: ET.Element | None, action: dict, *, pan_tag: str) -> int:
        if mixer is None:
            return 0
        local_changed = 0
        if action.get("gain_db_delta") is not None:
            node = mixer.find("./Volume/Manual")
            if node is not None:
                current = float(node.attrib.get("Value", "1"))
                new_val = current * db_to_amp(float(action["gain_db_delta"]))
                new_val = min(MAX_AMP, max(0.0, new_val))
                node.attrib["Value"] = f"{new_val:.10f}".rstrip("0").rstrip(".")
                local_changed += 1
        if action.get("pan") is not None:
            node = mixer.find(f"./{pan_tag}/Manual")
            if node is not None:
                pan = min(1.0, max(-1.0, float(action["pan"])))
                node.attrib["Value"] = f"{pan:.6f}".rstrip("0").rstrip(".")
                local_changed += 1
        return local_changed

    for track in list(tracks_parent):
        if track.tag not in {"MidiTrack", "AudioTrack", "GroupTrack"}:
            continue
        tid = track.attrib.get("Id", "")
        name = _effective_name(track)

        action = by_id.get(tid) or by_name.get(name)
        if action:
            changed += apply_to_mixer(_top_mixer(track), action, pan_tag="Pan")

        for drum in _direct_drum_devices(track):
            did = drum.attrib.get("Id", "")
            branches = drum.find("./Branches")
            if branches is None:
                continue
            for branch in list(branches):
                if branch.tag != "DrumBranch":
                    continue
                bid = branch.attrib.get("Id", "")
                synthetic = _synthetic_drum_id(tid, did, bid)
                branch_name = f"{name} / {_drum_branch_name(branch)}"
                branch_action = by_id.get(synthetic) or by_name.get(branch_name)
                if branch_action:
                    changed += apply_to_mixer(
                        _drum_branch_mixer(branch),
                        branch_action,
                        pan_tag="Panorama",
                    )

    if changed == 0:
        raise ValueError("Aucun réglage n'a pu être appliqué")

    output.parent.mkdir(parents=True, exist_ok=True)
    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with output.open("wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(xml_out)

    parse_als(output)
    return output


def drum_render_targets(project: ProjectInfo) -> list[TrackInfo]:
    return [
        t for t in project.tracks
        if t.target_kind == "drum_branch"
        and t.parent_track_id is not None
        and t.drum_device_id is not None
        and t.drum_branch_id is not None
        and t.physical_track_index is not None
    ]


def make_drum_branch_render_copy(
    source: str | Path,
    output: str | Path,
    target: TrackInfo,
) -> Path:
    """Create a disposable set where only one Drum Rack chain is audible."""
    if target.target_kind != "drum_branch":
        raise ValueError("La cible n'est pas une sous-piste de Drum Rack.")

    source = Path(source)
    output = Path(output)
    root, _ = _load_xml(source)
    tracks_parent = root.find("./LiveSet/Tracks")
    if tracks_parent is None:
        raise ValueError("Aucune section Tracks trouvée")

    matched = False
    for track in list(tracks_parent):
        if track.attrib.get("Id", "") != str(target.parent_track_id):
            continue
        for drum in _direct_drum_devices(track):
            if drum.attrib.get("Id", "") != str(target.drum_device_id):
                continue
            branches = drum.find("./Branches")
            if branches is None:
                continue
            for branch in list(branches):
                if branch.tag != "DrumBranch":
                    continue
                speaker = branch.find("./MixerDevice/Speaker/Manual")
                if speaker is None:
                    continue
                is_target = branch.attrib.get("Id", "") == str(target.drum_branch_id)
                speaker.attrib["Value"] = "true" if is_target else "false"
                if is_target:
                    matched = True

    if not matched:
        raise ValueError(f"Sous-instrument Drum Rack introuvable : {target.name}")

    output.parent.mkdir(parents=True, exist_ok=True)
    xml_out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with output.open("wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(xml_out)
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
