"""
system.py — small Windows integrations:
  * app identity (so the taskbar shows Nightwave's icon, not Python's)
  * single instance (launching again just brings the running widget back)
  * start with Windows
"""
import logging
import os
import socket
import sys
import threading
import time

log = logging.getLogger("nightwave.system")
IS_WIN = sys.platform == "win32"
APP_ID = "N-SilverJr.Nightwave.MediaWidget"
APP_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------------------------------------------ app identity
def set_app_id():
    """Give the process its own taskbar identity (otherwise it's grouped as 'Python')."""
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception as e:
        log.warning("could not set app id: %s", e)


# ------------------------------------------------------------ single instance
PORT = 48731
PREFIX, REPLY = b"nightwave:", b"nightwave-ok"


def signal_running_instance(command="show") -> bool:
    """If Nightwave is already running, send it a command ('show' or 'reset') and return True."""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.6) as s:
            s.settimeout(1.0)
            s.sendall(PREFIX + command.encode())
            return s.recv(32) == REPLY
    except Exception:
        return False


def listen_for_instances(on_command):
    """Run a tiny local listener; a second launch sends a command and we call on_command(cmd).
    If the port is briefly busy (e.g. right after a restart) keep retrying in the background."""

    def bind():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if IS_WIN:
            srv.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
        else:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", PORT))
        srv.listen(2)
        return srv

    def loop():
        srv = None
        for attempt in range(60):
            try:
                srv = bind()
                break
            except OSError as e:
                if attempt == 0:
                    log.info("single-instance port busy (%s); retrying", e)
                time.sleep(2)
        if srv is None:
            log.warning("single-instance listener not started")
            return
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    conn.settimeout(1.0)
                    msg = conn.recv(64)
                    if msg.startswith(PREFIX):
                        conn.sendall(REPLY)
                        on_command(msg[len(PREFIX):].decode(errors="ignore"))
            except Exception as e:
                log.debug("instance listener: %s", e)

    threading.Thread(target=loop, name="instance", daemon=True).start()


# ------------------------------------------------------------ start with Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Nightwave"


def _launch_command():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # no console window
    if os.path.isfile(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.join(APP_DIR, "main.py")}" --hidden'


def autostart_enabled() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
