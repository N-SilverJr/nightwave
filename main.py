"""
Nightwave — a floating now-playing widget for Windows.

Run:  python main.py      (or double-click run.bat)
"""
import json
import logging
import os
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), "Nightwave")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
LOG_FILE = os.path.join(DATA_DIR, "nightwave.log")

logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s: %(message)s")
log = logging.getLogger("nightwave")
sys.excepthook = lambda *a: log.error("uncaught:\n%s", "".join(traceback.format_exception(*a)))

import webview  # noqa: E402
try:
    from webview.window import FixPoint  # pywebview 5+
except Exception:
    FixPoint = None

from audio import AudioSpectrum  # noqa: E402
from media import MediaController  # noqa: E402
import system  # noqa: E402
import winpos  # noqa: E402
from tray import Tray  # noqa: E402

ICON_ICO = os.path.join(APP_DIR, "assets", "icon.ico")
ICON_TRAY = os.path.join(APP_DIR, "assets", "tray.png")

DEFAULTS = {"theme": "synthwave", "mode": "ring", "onTop": True, "compact": False,
            "size": [360, 430], "pos_px": None}
COMPACT_H = 96
MIN_W, MIN_H = 280, 300


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


class Api:
    """Every public method here can be called from the page as `pywebview.api.<name>()`.
    (Attributes starting with _ are hidden from the page.)"""

    def __init__(self, settings):
        self._settings = settings
        self._media = MediaController()
        self._audio = AudioSpectrum(is_playing=lambda: self._media.snapshot().get("playing"),
                                    log_file=LOG_FILE)
        self._window = None
        self._visible = True
        self._tray = Tray(self)

    # --- data for the UI ---
    def get_state(self):
        return self._media.snapshot()

    def get_art(self):
        return self._media.art()

    def get_spectrum(self):
        return self._audio.snapshot()

    # --- media control ---
    def control(self, action, value=None):
        return self._media.command(action, value)

    def select_session(self, app_id=None):
        self._media.select(app_id)
        return True

    # --- window ---
    def set_on_top(self, on):
        on = bool(on)
        self._settings["onTop"] = on
        # pywebview's own on_top setter changes the window from the wrong thread
        # on Windows (that crashed the app), so we use Win32 directly.
        if not winpos.set_topmost(self._window, on):
            self._window.on_top = on          # other platforms
        self._save()
        return on

    def set_compact(self, on):
        w = self._window
        on = bool(on)
        if on and not self._settings.get("compact"):
            self._settings["size"] = [w.width, w.height] if w.width else self._settings["size"]
        self._settings["compact"] = on
        if on:
            size = (max(MIN_W, self._settings["size"][0]), COMPACT_H)
        else:
            sw, sh = self._settings["size"]
            size = (max(MIN_W, sw), max(MIN_H, sh))
        if FixPoint is not None:   # keep the bottom edge where it is
            w.resize(*size, fix_point=FixPoint.SOUTH | FixPoint.WEST)
        else:
            w.resize(*size)
        self._save()
        return on

    def resize(self, width, height):
        width, height = max(MIN_W, int(width)), max(MIN_H, int(height))
        self._window.resize(width, height)
        return [width, height]

    def remember_size(self, width, height):
        self._settings["size"] = [max(MIN_W, int(width)), max(MIN_H, int(height))]
        self._save()

    def begin_drag(self):
        """Called when you start dragging: Windows follows the mouse until you let go."""
        moved = winpos.drag(self._window)
        if moved:
            self._remember_pos()
        return moved

    # fallback used only outside Windows
    def move_to(self, x, y):
        self._window.move(int(x), int(y))

    def end_drag(self):
        winpos.snap(self._window)
        self._remember_pos()

    def minimize(self):
        self._window.minimize()

    # --- show / hide / quit ---
    def has_tray(self):
        return self._tray.running

    def hide(self):
        """Hide to the tray (or minimise if the tray isn't available)."""
        if not self._tray.running:
            self._window.minimize()
            return "minimized"
        self._remember_pos()
        self._visible = False
        self._js("window.nwSetHidden && nwSetHidden(true)")
        self._window.hide()
        if not self._settings.get("tipTray"):
            self._settings["tipTray"] = True
            self._save()
            self._tray.notify("Still running in the tray. Click the icon to bring me back, "
                              "or right-click it to quit.")
        self._tray.refresh()
        return "tray"

    def show(self):
        self._visible = True
        self._window.show()
        try:
            self._window.restore()
        except Exception:
            pass
        winpos.set_topmost(self._window, bool(self._settings.get("onTop")))
        winpos.bring_to_front(self._window)
        self._js("window.nwSetHidden && nwSetHidden(false)")
        self._tray.refresh()

    def _on_instance_command(self, cmd):
        if cmd == "reset":
            self._settings.pop("pos_px", None)
            if self._settings.get("compact"):
                self._settings["compact"] = False
                self._js("window.nwSettingsChanged && nwSettingsChanged({compact: false})")
            self._settings["size"] = DEFAULTS["size"]
            self._window.resize(*DEFAULTS["size"])
            self._save()
            self.show()
            winpos.place(self._window, None)
        else:
            self.show()

    def toggle_visible(self):
        self.hide() if self._visible else self.show()

    def quit(self):
        self._remember_pos()
        self._tray.stop()
        self._window.destroy()

    def close(self):           # only used when there is no tray
        self.quit()

    def _js(self, code):
        try:
            self._window.evaluate_js(code)
        except Exception:
            pass

    def _push_settings(self):
        self._js(f"window.nwSettingsChanged && nwSettingsChanged({json.dumps({'onTop': bool(self._settings.get('onTop'))})})")

    # --- settings ---
    def load_settings(self):
        return {k: self._settings.get(k) for k in ("theme", "mode", "onTop", "compact", "tipDrag")}

    def save_settings(self, data):
        for k in ("theme", "mode", "tipDrag"):
            if k in (data or {}):
                self._settings[k] = data[k]
        self._save()

    def _remember_pos(self):
        if not self._visible:
            return
        rect = winpos.get_rect(self._window)
        if rect:
            self._settings["pos_px"] = [rect[0], rect[1]]
        self._save()

    def _save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except Exception as e:
            log.warning("could not save settings: %s", e)


def main():
    command = "reset" if "--reset" in sys.argv else "show"
    if system.signal_running_instance(command):   # already running → it handles it
        log.info("another instance is running; sent '%s'", command)
        return
    system.set_app_id()                        # own taskbar identity + icon

    settings = load_settings()
    if "--reset" in sys.argv:                  # rescue: forget saved position and size
        settings.pop("pos_px", None)
        settings["size"] = DEFAULTS["size"]
        settings["compact"] = False
    settings.pop("pos", None)                  # old (buggy) position format
    api = Api(settings)
    tray_ok = api._tray.start(ICON_TRAY)
    start_hidden = tray_ok and "--hidden" in sys.argv   # used by "Start with Windows"
    api._visible = not start_hidden

    w, h = settings["size"]
    if settings.get("compact"):
        h = COMPACT_H
    # No x/y here: the window opens centred (always visible), then we move it
    # using Windows' own pixel measurements once it exists.
    window = webview.create_window(
        "Nightwave",
        url=os.path.join(APP_DIR, "ui", "index.html"),
        js_api=api,
        width=w, height=h,
        min_size=(MIN_W, COMPACT_H),
        frameless=True, easy_drag=False, resizable=True,
        on_top=settings.get("onTop", True),
        background_color="#07010c",
        hidden=start_hidden,
    )
    api._window = window

    placed = {"done": False}

    def on_shown():
        if not placed["done"]:
            placed["done"] = True
            winpos.place(window, settings.get("pos_px"))

    window.events.shown += on_shown
    window.events.closing += api._remember_pos
    window.events.closed += api._tray.stop
    window.events.closed += api._audio.close
    system.listen_for_instances(api._on_instance_command)
    log.info("starting (tray=%s, hidden=%s)", tray_ok, start_hidden)
    webview.start(debug="--debug" in sys.argv,
                  icon=ICON_ICO if os.path.isfile(ICON_ICO) else None)


if __name__ == "__main__":
    main()
