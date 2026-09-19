from __future__ import annotations

import os
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

AUDIO_EXTS = {".wav", ".wave", ".aif", ".aiff", ".flac"}


class AbletonAutomationError(RuntimeError):
    pass


@dataclass
class ExportResult:
    folder: str
    files: list[str]
    elapsed_s: float
    diagnostic: str | None = None


def _emit(log: Callable[[str], None] | None, msg: str) -> None:
    if log:
        log(msg)


def _require_windows():
    if platform.system().lower() != "windows":
        raise AbletonAutomationError("L'automatisation d'Ableton de cette V11 est prévue pour Windows.")


def _imports():
    try:
        from pywinauto import Desktop, keyboard  # type: ignore
    except Exception as exc:
        raise AbletonAutomationError(
            "pywinauto n'est pas installé. Lance install.bat puis réessaie."
        ) from exc
    return Desktop, keyboard


def _all_top_windows(backend: str = "uia"):
    Desktop, _ = _imports()
    return Desktop(backend=backend).windows()


def _window_text(w) -> str:
    try:
        return w.window_text() or ""
    except Exception:
        return ""


def _looks_like_live_window(w) -> bool:
    title = _window_text(w).lower()
    if "ableton live" in title:
        return True
    try:
        proc = w.process_id()
        import psutil  # type: ignore
        name = psutil.Process(proc).name().lower()
        return "ableton live" in name
    except Exception:
        return False


def find_live_window(project_stem: str | None = None, *, strict: bool = False):
    wins = [w for w in _all_top_windows() if _looks_like_live_window(w)]
    if not wins:
        return None
    if project_stem:
        needle = project_stem.lower()
        preferred = [w for w in wins if needle in _window_text(w).lower()]
        if preferred:
            return preferred[0]
        if strict:
            return None
    # Prefer a normal, visible, sizeable main window.
    for w in wins:
        try:
            if w.is_visible() and w.rectangle().width() > 700:
                return w
        except Exception:
            pass
    return wins[0]


def _wait_for_live(project_stem: str, timeout: float, log=None):
    end = time.time() + timeout
    last_notice = 0.0
    while time.time() < end:
        w = find_live_window(project_stem, strict=True)
        if w is not None:
            return w
        if time.time() - last_notice > 8:
            _emit(log, "J'attends qu'Ableton Live ouvre le projet…")
            last_notice = time.time()
        time.sleep(1.0)
    raise AbletonAutomationError(
        "Ableton Live ne s'est pas ouvert à temps. Ouvre le projet dans Live puis relance l'étape."
    )


def launch_or_focus_project(project_path: str | Path, *, timeout: float = 180.0, log=None):
    _require_windows()
    project_path = Path(project_path).resolve()
    if not project_path.exists():
        raise AbletonAutomationError(f"Projet introuvable : {project_path}")

    w = find_live_window(project_path.stem, strict=True)
    if w is None:
        _emit(log, f"Ouverture de {project_path.name} dans Ableton Live…")
        try:
            os.startfile(str(project_path))  # type: ignore[attr-defined]
        except Exception as exc:
            raise AbletonAutomationError(
                "Windows n'arrive pas à ouvrir le .als. Vérifie que .als est associé à Ableton Live."
            ) from exc
        w = _wait_for_live(project_path.stem, timeout, log=log)
    else:
        _emit(log, "Le bon projet Ableton est déjà ouvert.")

    try:
        w.restore()
    except Exception:
        pass
    try:
        w.set_focus()
    except Exception:
        try:
            w.click_input()
        except Exception:
            pass
    time.sleep(1.0)
    return w


def _find_export_dialog(process_id: int, timeout: float = 30.0):
    """Find Live's *actual* Export Audio/Video modal.

    Important: never treat the main Live window as the export dialog merely
    because the project filename contains words such as ``render``.  AutoMix
    intentionally creates files named render_source_pass_XX.als, so V4 could
    mistake the main window for the modal and then click the File menu.
    """
    end = time.time() + timeout
    # Deliberately DO NOT include the generic word "render" here.
    title_re = re.compile(
        r"(exporter\s+audio(?:/|\s*)vid[eé]o|export\s+audio(?:/|\s*)video|"
        r"export\s+audio|audio(?:/|\s*)vid[eé]o|conversion\s+audio)",
        re.I,
    )
    while time.time() < end:
        for w in _all_top_windows():
            try:
                if w.process_id() != process_id or not w.is_visible():
                    continue
            except Exception:
                continue

            title = _window_text(w).strip()
            low_title = title.lower()

            # The normal project window always contains "Ableton Live" in its
            # title.  A filename like render_source_pass_00 must not make it a
            # candidate for the export modal.
            looks_like_main = "ableton live" in low_title
            if title_re.search(title) and not looks_like_main:
                return w

            # Fallback for themes/builds where the modal title is not exposed:
            # require a compact top-level window with an actual Export/Exporter
            # *button* plus another export-dialog signature.  Menu items in the
            # main Live window are not Buttons, so they won't trigger this.
            try:
                r = w.rectangle()
                buttons = [(_window_text(b) or "").strip() for b in w.descendants(control_type="Button")]
                has_export_button = any(re.fullmatch(r"export(?:er)?", b, re.I) for b in buttons)
                combo_count = len(w.descendants(control_type="ComboBox"))
                compact = r.width() < 760
                if not looks_like_main and compact and has_export_button and combo_count >= 1:
                    return w
            except Exception:
                pass
        time.sleep(0.35)
    return None


def _dump_dialog(dialog) -> str:
    lines = [f"TITLE: {_window_text(dialog)!r}"]
    try:
        for c in dialog.descendants():
            try:
                lines.append(
                    f"{getattr(c.element_info, 'control_type', '?'):12} | {c.window_text()!r} | auto_id={getattr(c.element_info, 'automation_id', '')!r}"
                )
            except Exception:
                pass
            if len(lines) > 250:
                break
    except Exception as exc:
        lines.append(f"dump error: {exc}")
    return "\n".join(lines)


def _combo_items(combo) -> list[str]:
    out: list[str] = []
    try:
        combo.expand()
        time.sleep(0.2)
    except Exception:
        pass
    try:
        for item in combo.descendants(control_type="ListItem"):
            t = item.window_text().strip()
            if t:
                out.append(t)
    except Exception:
        pass
    if not out:
        try:
            out = [x for x in combo.item_texts() if x]
        except Exception:
            pass
    try:
        combo.collapse()
    except Exception:
        pass
    return out


def _sorted_controls(dialog, control_type: str):
    """Return visible controls sorted top-to-bottom / left-to-right.

    Ableton uses some custom UI controls whose popup entries are not always
    exposed through UI Automation.  Sorting by rectangle gives us a stable
    way to identify the topmost combo (Rendered Track) without depending on
    localized labels.
    """
    try:
        controls = [c for c in dialog.descendants(control_type=control_type) if c.is_visible()]
    except Exception:
        return []

    def key(c):
        try:
            r = c.rectangle()
            return (r.top, r.left)
        except Exception:
            return (10**9, 10**9)

    return sorted(controls, key=key)


def _select_combo_second_item_with_keyboard(combo, keyboard) -> bool:
    """Select item #2 in a combo without reading its popup text.

    In Live 12's Rendered Track menu the first entry is Master/Main and the
    second entry is All Individual Tracks.  This keyboard route works even
    when Ableton's custom popup does not publish ListItem children to UIA.
    """
    try:
        combo.set_focus()
    except Exception:
        try:
            combo.click_input()
        except Exception:
            return False
    time.sleep(0.15)

    # HOME gives us a deterministic starting point regardless of the value
    # remembered by Live from a previous export.
    try:
        keyboard.send_keys('{HOME}{DOWN}{ENTER}', pause=0.08)
        time.sleep(0.25)
        return True
    except Exception:
        return False


def _select_rendered_track_by_geometry(dialog, keyboard) -> bool:
    """Last-resort click on Live's Rendered Track dropdown by dialog geometry.

    The Export Audio/Video window is a fixed layout.  We use proportions of
    the dialog rectangle rather than absolute screen coordinates so Windows
    scaling / window placement do not matter.  The click lands in the first
    dropdown (Rendered Track), then HOME/DOWN/ENTER chooses item #2.
    """
    try:
        r = dialog.rectangle()
        # From Live 12's export dialog: the first combo sits around 41% of the
        # width and 9% of the height from the top-left of the modal.
        x = int(r.width() * 0.73)
        y = int(r.height() * 0.118)
        dialog.click_input(coords=(x, y))
        time.sleep(0.2)
        keyboard.send_keys('{HOME}{DOWN}{ENTER}', pause=0.08)
        time.sleep(0.3)
        return True
    except Exception:
        return False


def _select_all_individual_tracks(dialog) -> bool:
    """Choose All Individual Tracks using the same interaction as a human.

    The Live 12 dropdown is custom-drawn and UIA selection can succeed without
    changing the visible value. V14 therefore clicks the actual dropdown,
    resets to the first entry with Home, then moves once to the second entry.
    """
    _, keyboard = _imports()

    # Exact geometry measured from the user's Live 12 Export Audio/Video window.
    if not _dialog_click_rel(dialog, 0.73, 0.118):
        return False
    time.sleep(0.25)
    try:
        keyboard.send_keys("{HOME}{DOWN}{ENTER}", pause=0.10)
        time.sleep(0.45)
        return True
    except Exception:
        return False


def _select_specific_rendered_track(dialog, track_name: str, track_index: int | None) -> bool:
    """Select one physical Live track in the Rendered Track chooser."""
    _, keyboard = _imports()
    combos = _sorted_controls(dialog, "ComboBox")
    if not combos:
        return False
    combo = combos[0]

    wanted = (track_name or "").strip().lower()
    items = _combo_items(combo)
    if items and wanted:
        for item in items:
            if item.strip().lower() == wanted:
                try:
                    combo.select(item)
                    time.sleep(0.2)
                    return True
                except Exception:
                    pass
        for item in items:
            low = item.strip().lower()
            if wanted in low or low in wanted:
                try:
                    combo.select(item)
                    time.sleep(0.2)
                    return True
                except Exception:
                    pass

    if track_index is not None:
        # Live 12: Main, All Individual Tracks, Selected Tracks Only, then tracks.
        option_index = 3 + int(track_index)
        try:
            combo.select(option_index)
            time.sleep(0.25)
        except Exception:
            pass
        try:
            combo.set_focus()
        except Exception:
            try:
                combo.click_input()
            except Exception:
                return False
        try:
            keyboard.send_keys("{HOME}", pause=0.05)
            for _ in range(option_index):
                keyboard.send_keys("{DOWN}", pause=0.025)
            keyboard.send_keys("{ENTER}", pause=0.05)
            time.sleep(0.25)
            return True
        except Exception:
            pass

    return False



def _dialog_click_rel(dialog, rel_x: float, rel_y: float) -> bool:
    """Perform a real mouse click at a normalized point of Live's modal.

    Live's Export Audio/Video UI is mostly custom-drawn. UI Automation can report
    success even when Live did not actually react. V14 therefore converts the
    point to absolute screen coordinates and uses the Windows mouse input path,
    matching what the user does manually.
    """
    try:
        from pywinauto import mouse  # type: ignore
        r = dialog.rectangle()
        x = int(r.left + r.width() * rel_x)
        y = int(r.top + r.height() * rel_y)
        mouse.click(button="left", coords=(x, y))
        return True
    except Exception:
        return False


def _custom_toggle_state(dialog, rel_x: float, rel_y: float) -> bool | None:
    """Infer Live's custom On/Off button state from its fill colour.

    Live colours enabled toggles (cyan/yellow in the default theme) while Off
    toggles are grey.  This avoids OCR and works even when UI Automation exposes
    no semantic state for the control.  Returns None when a screenshot cannot be
    captured reliably.
    """
    try:
        import colorsys
        img = dialog.capture_as_image().convert('RGB')
        x = int(img.width * rel_x)
        y = int(img.height * rel_y)
        x0, x1 = max(0, x-22), min(img.width, x+22)
        y0, y1 = max(0, y-7), min(img.height, y+7)
        sats = []
        for yy in range(y0, y1, 2):
            for xx in range(x0, x1, 2):
                rr, gg, bb = img.getpixel((xx, yy))
                # Ignore near-black text/borders and near-white highlights.
                vmax, vmin = max(rr,gg,bb), min(rr,gg,bb)
                if vmax < 45 or vmin > 235:
                    continue
                _h, sat, _v = colorsys.rgb_to_hsv(rr/255.0, gg/255.0, bb/255.0)
                sats.append(sat)
        if not sats:
            return None
        sats.sort()
        # A coloured Live toggle has a large high-saturation area; grey Off does not.
        probe = sats[int(len(sats) * 0.75)]
        return probe >= 0.22
    except Exception:
        return None


def _ensure_custom_toggle(dialog, *, rel_y: float, desired: bool, log=None, name: str = '') -> bool:
    rel_x = 0.84
    state = _custom_toggle_state(dialog, rel_x, rel_y)
    if state is None:
        _emit(log, f"État du réglage {name or rel_y} non lisible visuellement ; je le laisse tel quel.")
        return False
    if state != desired:
        if not _dialog_click_rel(dialog, rel_x, rel_y):
            return False
        time.sleep(0.18)
        state2 = _custom_toggle_state(dialog, rel_x, rel_y)
        if state2 is not None and state2 != desired:
            return False
    _emit(log, f"{name}: {'On' if desired else 'Off'}")
    return True


def _configure_live12_export_by_geometry(dialog, log=None) -> None:
    """Set the Live 12 export toggles needed for analysis stems.

    These y positions are normalized from Live 12's Export Audio/Video modal and
    remain stable when the dialog is moved or Windows scaling changes.  Semantic
    UIA controls are still preferred elsewhere; this only handles Live's custom
    painted On/Off widgets.
    """
    # Return/master off; loop off; mono off; normalize off; analysis off.
    for name, y, desired in [
        ('Retours + effets Master', 0.274, False),
        ('Convertir en boucle', 0.309, False),
        ('Convertir en mono', 0.345, False),
        ('Normaliser', 0.381, False),
        ("Créer fichier d'analyse", 0.418, False),
        ('Encoder en PCM', 0.607, True),
        ('Encoder en MP3', 0.756, False),
        ('Créer vidéo', 0.836, False),
    ]:
        _ensure_custom_toggle(dialog, rel_y=y, desired=desired, log=log, name=name)


def _control_name(control) -> str:
    """Best-effort accessible name for a Live control.

    Live 12 sometimes exposes custom-drawn controls as Text/Custom rather than
    Button.  Looking only at ``control_type=Button`` therefore misses the
    visible Exporter label on some builds/themes.
    """
    parts = []
    try:
        t = control.window_text()
        if t:
            parts.append(str(t))
    except Exception:
        pass
    try:
        name = getattr(control.element_info, 'name', '')
        if name and str(name) not in parts:
            parts.append(str(name))
    except Exception:
        pass
    return ' '.join(parts).strip()


def _click_named_export_control(dialog) -> bool:
    """Click any accessible control whose name is exactly Export/Exporter.

    This deliberately searches *all* control types, not only UIA Buttons.
    """
    try:
        controls = dialog.descendants()
    except Exception:
        controls = []
    candidates = []
    for c in controls:
        name = _control_name(c).strip().lower().replace('&', '')
        name = name.rstrip('.… ').strip()
        if not re.fullmatch(r'export(?:er)?', name, re.I):
            continue
        try:
            if not c.is_visible() or not c.is_enabled():
                continue
        except Exception:
            pass
        try:
            r = c.rectangle()
            candidates.append((r.top, r.left, c))
        except Exception:
            candidates.append((0, 0, c))

    # The real action button is normally the bottom-most matching control.
    for _top, _left, c in sorted(candidates, key=lambda row: (row[0], row[1]), reverse=True):
        for method in ('invoke', 'click_input'):
            try:
                fn = getattr(c, method)
                fn()
                time.sleep(0.35)
                try:
                    if not dialog.is_visible():
                        return True
                except Exception:
                    return True
            except Exception:
                continue
    return False


def _click_export_left_of_cancel(dialog) -> bool:
    """Infer Exporter's position from the accessible Annuler/Cancel button.

    In Live's export dialog Exporter sits immediately to the left of Cancel.
    This is more robust than a fixed x-coordinate when Windows scaling or Live
    changes the width of the dialog.
    """
    try:
        controls = dialog.descendants()
        dr = dialog.rectangle()
    except Exception:
        return False
    for c in controls:
        name = _control_name(c).strip().lower().replace('&', '')
        name = name.rstrip('.… ').strip()
        if not re.fullmatch(r'(cancel|annuler)', name, re.I):
            continue
        try:
            if not c.is_visible():
                continue
            r = c.rectangle()
            width = max(45, r.width())
            # One button-width + a small gap to the left, same vertical centre.
            sx = int(r.left - max(8, width * 0.18) - width / 2)
            sy = int((r.top + r.bottom) / 2)
            rel_x = (sx - dr.left) / max(1, dr.width())
            rel_y = (sy - dr.top) / max(1, dr.height())
            if 0.05 < rel_x < 0.95 and 0.75 < rel_y < 1.0:
                if _dialog_click_rel(dialog, rel_x, rel_y):
                    time.sleep(0.35)
                    try:
                        if not dialog.is_visible():
                            return True
                    except Exception:
                        return True
        except Exception:
            continue
    return False


def _click_export(dialog, keyboard=None) -> bool:
    """Press Live's Export/Exporter button using several independent routes.

    The automatic path is best-effort only. V12 no longer waits for manual
    intervention when Live's custom export UI cannot be detected.
    """
    # 1) Normal UIA Button.  Do not trust a successful click call unless the
    # export modal actually disappears.
    if _click_button(dialog, (r"^export$", r"^exporter$")):
        time.sleep(0.35)
        try:
            if not dialog.is_visible():
                return True
        except Exception:
            return True

    # 2) Live may expose the visible label as Text/Custom instead of Button.
    if _click_named_export_control(dialog):
        return True

    # 3) If Cancel is accessible, infer Exporter's location immediately left.
    if _click_export_left_of_cancel(dialog):
        return True

    # 4) Geometry fallbacks.  Keep them on the left/centre portion of the
    # bottom row so we do not accidentally hit Cancel on layouts where it is
    # the rightmost button.
    for x, y in (
        # User's Live 12 French dialog: Exporter is around 38% of modal width.
        (0.38, 0.958),
        (0.40, 0.958),
        (0.36, 0.958),
        (0.42, 0.958),
        (0.38, 0.946),
    ):
        if _dialog_click_rel(dialog, x, y):
            time.sleep(0.35)
            try:
                if not dialog.is_visible():
                    return True
            except Exception:
                return True

    # 5) Last-resort keyboard activation after focusing the most likely area.
    if keyboard is not None:
        for x in (0.38, 0.40, 0.36):
            try:
                _dialog_click_rel(dialog, x, 0.958)
                keyboard.send_keys('{ENTER}', pause=0.05)
                time.sleep(0.35)
                try:
                    if not dialog.is_visible():
                        return True
                except Exception:
                    return True
            except Exception:
                pass
    return False

def _set_checkbox(dialog, keywords: tuple[str, ...], desired: bool) -> bool:
    try:
        checks = dialog.descendants(control_type="CheckBox")
    except Exception:
        return False
    for cb in checks:
        text = _window_text(cb).lower()
        if not any(k in text for k in keywords):
            continue
        try:
            state = cb.get_toggle_state()
            checked = bool(state)
        except Exception:
            checked = None
        if checked is None or checked != desired:
            try:
                cb.click_input()
            except Exception:
                try:
                    cb.toggle()
                except Exception:
                    continue
        return True
    return False


def _click_button(dialog, patterns: tuple[str, ...]) -> bool:
    try:
        buttons = dialog.descendants(control_type="Button")
    except Exception:
        buttons = []
    for b in buttons:
        text = _window_text(b).strip().lower()
        if any(re.fullmatch(p, text, re.I) or re.search(p, text, re.I) for p in patterns):
            try:
                if b.is_enabled():
                    b.click_input()
                    return True
            except Exception:
                pass
    return False


def _find_save_dialog(process_id: int | None, timeout: float = 30.0):
    """Find the Windows Save As dialog opened by Live.

    Prefer Live's process, but allow the common-file-dialog host as a fallback
    because some Windows 11 configurations expose it under a different PID.
    """
    end = time.time() + timeout
    title_re = re.compile(r"(save|enregistrer|choose|choisir|export)", re.I)
    while time.time() < end:
        candidates = []
        for w in _all_top_windows():
            try:
                if not w.is_visible():
                    continue
                same_pid = process_id is None or w.process_id() == process_id
            except Exception:
                continue
            title = _window_text(w)
            if not title_re.search(title):
                continue
            try:
                edits = [e for e in w.descendants(control_type="Edit") if e.is_visible()]
                buttons = [(_window_text(x) or '').lower() for x in w.descendants(control_type="Button")]
                has_save = any(("save" in x or "enregistrer" in x) for x in buttons)
                if edits and has_save:
                    candidates.append((0 if same_pid else 1, w))
            except Exception:
                pass
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]
        time.sleep(0.35)
    return None

def _set_save_target(dialog, output_folder: Path, base_name: str, log=None) -> None:
    """Force the Save dialog into AutoMix's project folder and save.

    V8 navigates the dialog to the folder first (Ctrl+L), then enters only the
    base filename.  This is more deterministic than relying on Live's last
    export directory or hoping an absolute path in the filename box is parsed.
    """
    _, keyboard = _imports()
    output_folder = Path(output_folder).resolve()
    output_folder.mkdir(parents=True, exist_ok=True)
    _emit(log, f"Fenêtre Enregistrer détectée. Destination forcée : {output_folder}")

    try:
        dialog.set_focus()
    except Exception:
        pass

    # Navigate the common Windows dialog to the exact AutoMix folder.
    navigated = False
    try:
        keyboard.send_keys('^l', pause=0.05)
        time.sleep(0.15)
        keyboard.send_keys('^a', pause=0.03)
        keyboard.send_keys(str(output_folder), with_spaces=True, pause=0.01)
        keyboard.send_keys('{ENTER}', pause=0.05)
        time.sleep(0.8)
        navigated = True
    except Exception:
        pass

    edits = []
    try:
        edits = [e for e in dialog.descendants(control_type="Edit") if e.is_visible() and e.is_enabled()]
    except Exception:
        pass
    if not edits:
        raise AbletonAutomationError("Impossible de trouver le champ de nom de fichier dans la fenêtre Enregistrer.")

    # Prefer a semantically named filename box. Otherwise use the bottom-most
    # visible edit, which is the filename field in the Windows Save dialog.
    chosen = None
    for e in edits:
        label = (_window_text(e) + " " + str(getattr(e.element_info, "name", ""))).lower()
        if any(k in label for k in ("file name", "nom du fichier", "filename", "nom de fichier")):
            chosen = e
            break
    if chosen is None:
        def edit_key(e):
            try:
                r = e.rectangle()
                return (r.top, r.left)
            except Exception:
                return (-1, -1)
        chosen = sorted(edits, key=edit_key)[-1]

    # If navigation worked, enter just a safe basename. Otherwise fall back to
    # an absolute target path so the destination still cannot drift elsewhere.
    value = base_name if navigated else str(output_folder / base_name)
    try:
        chosen.set_focus()
    except Exception:
        pass
    try:
        chosen.set_edit_text(value)
    except Exception:
        try:
            chosen.click_input()
            keyboard.send_keys('^a', pause=0.03)
            keyboard.send_keys(value, with_spaces=True, pause=0.01)
        except Exception as exc:
            raise AbletonAutomationError("Je n'arrive pas à saisir le nom d'export dans la fenêtre Enregistrer.") from exc

    # Prefer the real Save button; Enter is a robust fallback in common dialogs.
    if not _click_button(dialog, (r"^save$", r"^enregistrer$", r"enregistrer", r"save")):
        try:
            chosen.set_focus()
            keyboard.send_keys('{ENTER}', pause=0.05)
        except Exception as exc:
            raise AbletonAutomationError("Je n'arrive pas à valider Enregistrer dans la fenêtre de sauvegarde.") from exc
    time.sleep(0.6)

def _audio_signature(folder: Path):
    rows = []
    if not folder.exists():
        return ()
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            try:
                st = p.stat()
                rows.append((str(p), st.st_size, st.st_mtime_ns))
            except OSError:
                pass
    return tuple(sorted(rows))


def wait_for_render(folder: str | Path, *, min_files: int = 1, timeout: float = 1800.0, log=None) -> list[Path]:
    folder = Path(folder)
    start = time.time()
    last_sig = None
    stable = 0
    announced = False
    while time.time() - start < timeout:
        sig = _audio_signature(folder)
        if len(sig) >= min_files:
            if not announced:
                _emit(log, f"Ableton écrit {len(sig)} fichier(s) audio…")
                announced = True
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            if stable >= 3:
                return [Path(x[0]) for x in sig]
        time.sleep(2.0)
    raise AbletonAutomationError(
        "Le rendu n'a pas terminé dans le délai prévu. Vérifie si Ableton affiche une fenêtre ou un message bloquant."
    )


def export_individual_tracks(
    project_path: str | Path,
    output_folder: str | Path,
    *,
    expected_min_files: int = 1,
    log: Callable[[str], None] | None = None,
    launch_timeout: float = 180.0,
    render_timeout: float = 1800.0,
    rendered_track_name: str | None = None,
    rendered_track_index: int | None = None,
    base_name: str = "AUTOMIX_STEMS",
) -> ExportResult:
    """Drive Ableton Live's own Export Audio/Video dialog on Windows.

    This deliberately uses Live's renderer, so MIDI instruments and plug-ins are
    heard exactly by Live. It does not synthesize stems from the ALS XML itself.
    """
    _require_windows()
    _, keyboard = _imports()
    project_path = Path(project_path).resolve()
    output_folder = Path(output_folder).resolve()
    output_folder.mkdir(parents=True, exist_ok=True)

    # Unique work folder should be empty. Removing only audio files avoids touching user data.
    for p in output_folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            try:
                p.unlink()
            except OSError:
                pass

    started = time.time()
    main = launch_or_focus_project(project_path, timeout=launch_timeout, log=log)
    pid = main.process_id()
    # If the previous attempt stopped on the export modal, reuse it instead
    # of sending Ctrl+Shift+R into an already-modal Live window.
    dialog = _find_export_dialog(pid, timeout=1.0)
    if dialog is None:
        _emit(log, "Ouverture de la fenêtre Export Audio/Vidéo…")
        try:
            main.set_focus()
        except Exception:
            pass
        # Close any menu left open by a previous failed automation before
        # sending Live's Export shortcut.
        try:
            keyboard.send_keys('{ESC}{ESC}', pause=0.05)
        except Exception:
            pass
        time.sleep(0.15)
        keyboard.send_keys("^+r")
        dialog = _find_export_dialog(pid, timeout=6.0)
    else:
        _emit(log, "Fenêtre d'export Ableton déjà ouverte : je la réutilise.")

    if dialog is None:
        raise AbletonAutomationError(
            "AutoMix n'arrive pas à détecter la fenêtre Export Audio/Vidéo sur cette installation de Live. "
            "Le mode veille a été supprimé en V14. Exporte les stems toi-même, puis utilise "
            "« Importer des stems déjà exportés » dans AutoMix."
        )

    diagnostic = _dump_dialog(dialog)
    try:
        (output_folder / "ableton_export_dialog.txt").write_text(diagnostic, encoding="utf-8")
    except Exception:
        pass
    if rendered_track_name is None:
        _emit(log, "Fenêtre d'export détectée. Je choisis toutes les pistes individuelles…")
        if not _select_all_individual_tracks(dialog):
            raise AbletonAutomationError(
                "J'ai ouvert l'export Ableton, mais je n'ai pas reconnu l'option 'Toutes les pistes individuelles'.\n\n"
                "Même les méthodes de secours n'ont pas réussi à piloter le menu Rendered Track. Un diagnostic a été enregistré."
            )
    else:
        _emit(log, f"Fenêtre d'export détectée. Je rends uniquement : {rendered_track_name}")
        if not _select_specific_rendered_track(dialog, rendered_track_name, rendered_track_index):
            raise AbletonAutomationError(
                f"Je n'arrive pas à sélectionner la piste « {rendered_track_name} » dans le menu Rendered Track."
            )

    # A mix-analysis render should be WAV/PCM, stereo, non-normalized, with
    # MP3/video disabled.  First try semantic UI controls, then Live 12's
    # custom-drawn switches via visual state + normalized geometry.
    _set_checkbox(dialog, ("normaliz", "normaliser"), False)
    _set_checkbox(dialog, ("mono",), False)
    _set_checkbox(dialog, ("analysis", "analyse"), False)
    _configure_live12_export_by_geometry(dialog, log=log)

    # Press Export and *verify* that the Save dialog actually appeared.
    # V8 retries the complete strategy (semantic control, any named control,
    # position inferred from Cancel, then geometry) rather than repeating the
    # same hard-coded coordinate.
    try:
        dialog.capture_as_image().save(output_folder / "ableton_export_before_click.png")
    except Exception:
        pass

    clicked = _click_export(dialog, keyboard=keyboard)
    save = _find_save_dialog(pid, timeout=4.0)
    if save is None:
        _emit(log, "Exporter n'a pas encore ouvert Enregistrer ; je relance une deuxième stratégie complète…")
        try:
            dialog.set_focus()
        except Exception:
            pass
        clicked = _click_export(dialog, keyboard=keyboard) or clicked
        save = _find_save_dialog(pid, timeout=4.0)

    if save is None:
        try:
            dialog.capture_as_image().save(output_folder / "ableton_export_failed.png")
        except Exception:
            pass
        raise AbletonAutomationError(
            "AutoMix n'arrive pas à ouvrir ou détecter la fenêtre Enregistrer après Exporter. "
            "Le mode veille a été supprimé en V14. Termine l'export toi-même dans Ableton, puis utilise "
            "« Importer des stems déjà exportés » dans AutoMix. "
            "Une capture ableton_export_failed.png a été enregistrée dans le dossier des stems."
        )

    _emit(log, "Fenêtre Enregistrer détectée : je reprends automatiquement la main.")
    _set_save_target(save, output_folder, base_name, log=log)
    _emit(log, "Rendu lancé par Ableton. J'attends la fin sans te demander d'intervenir…")

    files = wait_for_render(
        output_folder,
        min_files=max(1, expected_min_files),
        timeout=render_timeout,
        log=log,
    )
    elapsed = time.time() - started
    _emit(log, f"Rendu terminé : {len(files)} fichier(s) en {elapsed:.0f} s.")
    return ExportResult(str(output_folder), [str(p) for p in files], elapsed, diagnostic)
