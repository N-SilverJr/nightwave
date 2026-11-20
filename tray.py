"""
tray.py — the Nightwave icon in the taskbar's notification area ("hidden icons").

Left-click  → show / hide the widget
Right-click → menu: playback controls, always on top, start with Windows, quit
"""
import logging
import threading
import time

import system

log = logging.getLogger("nightwave.tray")

try:
    import pystray
    from PIL import Image
    TRAY_OK, TRAY_ERR = True, None
except Exception as e:
    TRAY_OK, TRAY_ERR = False, e


class Tray:
    def __init__(self, app):
        self.app = app          # main.Api
        self.icon = None
        self._last_sig = None

    @property
    def running(self):
        return self.icon is not None

    def start(self, image_path):
        if not TRAY_OK:
            log.warning("tray unavailable: %s", TRAY_ERR)
            return False
        try:
            image = Image.open(image_path)
            M, Menu = pystray.MenuItem, pystray.Menu
            playing = lambda: bool(self.app._media.snapshot().get("playing"))  # noqa: E731
            menu = Menu(
                M(lambda item: "Hide Nightwave" if self.app._visible else "Show Nightwave",
                  self._toggle, default=True),
                Menu.SEPARATOR,
                M(lambda item: "Pause" if playing() else "Play", lambda: self.app.control("toggle")),
                M("Next", lambda: self.app.control("next")),
                M("Previous", lambda: self.app.control("prev")),
                Menu.SEPARATOR,
                M("Always on top", self._toggle_top, checked=lambda item: bool(self.app._settings.get("onTop"))),
                M("Start with Windows", self._toggle_autostart, checked=lambda item: system.autostart_enabled(),
                  visible=system.IS_WIN),
                Menu.SEPARATOR,
                M("Quit Nightwave", self._quit),
            )
            self.icon = pystray.Icon("Nightwave", image, "Nightwave", menu)
            threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
            threading.Thread(target=self._refresh_loop, name="tray-refresh", daemon=True).start()
            return True
        except Exception as e:
            log.warning("could not start tray: %s", e)
            self.icon = None
            return False

    def notify(self, message, title="Nightwave"):
        try:
            if self.icon is not None and getattr(self.icon, "HAS_NOTIFICATION", False):
                self.icon.notify(message, title)
        except Exception as e:
            log.debug("notify failed: %s", e)

    def stop(self):
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        self.icon = None

    def refresh(self):
        try:
            if self.icon is not None:
                self.icon.update_menu()
        except Exception:
            pass

    # ---------- menu actions ----------
    def _toggle(self):
        self.app.toggle_visible()

    def _toggle_top(self):
        self.app.set_on_top(not self.app._settings.get("onTop"))
        self.app._push_settings()

    def _toggle_autostart(self):
        try:
            system.set_autostart(not system.autostart_enabled())
        except Exception as e:
            log.warning("autostart change failed: %s", e)
        self.refresh()

    def _quit(self):
        self.app.quit()

    # ---------- keep tooltip + menu labels current ----------
    def _refresh_loop(self):
        while self.icon is not None:
            try:
                s = self.app._media.snapshot()
                if s.get("active"):
                    tip = " · ".join(x for x in (s.get("title"), s.get("artist")) if x) or s.get("app", "")
                    tip = f"Nightwave — {tip}"
                else:
                    tip = "Nightwave — nothing playing"
                if len(tip) > 120:
                    tip = tip[:119] + "…"
                if self.icon.title != tip:
                    self.icon.title = tip
                sig = (s.get("playing"), self.app._visible, self.app._settings.get("onTop"))
                if sig != self._last_sig:
                    self._last_sig = sig
                    self.icon.update_menu()
            except Exception as e:
                log.debug("tray refresh: %s", e)
            time.sleep(1.5)
