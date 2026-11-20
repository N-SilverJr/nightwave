"""
winpos.py — puts the widget in the bottom-right corner (or where you left it).

Uses the Win32 API directly so every number is in the same unit (real screen
pixels). This avoids the bug where display scaling (125%, 150%...) pushed the
window off the edge of the screen.
If anything here fails, the window simply stays centred, so it is never lost.
"""
import ctypes
import logging
import sys
import threading
import time

log = logging.getLogger("nightwave.winpos")

MARGIN = 16                    # logical px from the screen edge
MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTONEAREST = 2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
VK_LBUTTON, VK_RBUTTON, SM_SWAPBUTTON = 0x01, 0x02, 23

if sys.platform == "win32":
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    _u = ctypes.windll.user32
    _u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u.GetWindowRect.restype = wintypes.BOOL
    _u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _u.MonitorFromWindow.restype = wintypes.HMONITOR
    _u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _u.MonitorFromPoint.restype = wintypes.HMONITOR
    _u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    _u.GetMonitorInfoW.restype = wintypes.BOOL
    _u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _u.SetWindowPos.restype = wintypes.BOOL
    _u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _u.GetCursorPos.restype = wintypes.BOOL
    _u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u.GetAsyncKeyState.restype = ctypes.c_short
    _u.GetSystemMetrics.argtypes = [ctypes.c_int]
    _u.GetSystemMetrics.restype = ctypes.c_int
    try:
        _u.GetDpiForWindow.argtypes = [wintypes.HWND]
        _u.GetDpiForWindow.restype = wintypes.UINT
    except AttributeError:      # Windows 10 before 1607
        pass


_hwnd_cache = {}


def _hwnd(window):
    key = id(window)
    if key in _hwnd_cache:
        return _hwnd_cache[key]
    try:
        h = int(window.native.Handle.ToInt64())
        _hwnd_cache[key] = h
        return h
    except Exception as e:
        log.warning("no native window handle: %s", e)
        return None


def get_rect(window):
    """(left, top, width, height) in real pixels, or None."""
    if sys.platform != "win32":
        return None
    hwnd = _hwnd(window)
    if not hwnd:
        return None
    r = wintypes.RECT()
    if not _u.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _on_a_screen(x, y, w):
    # the top edge (where you'd grab it) must be on some monitor
    for px in (x + 20, x + w // 2, x + w - 20):
        if _u.MonitorFromPoint(wintypes.POINT(px, y + 10), MONITOR_DEFAULTTONULL):
            return True
    return False


SNAP = 24                      # logical px: closer than this to an edge → snap to it
EDGE_GAP = 8                   # logical px gap left when snapped


def snap(window):
    """After a drag: keep the window fully on its screen and snap it to nearby edges."""
    if sys.platform != "win32":
        return
    try:
        hwnd = _hwnd(window)
        rect = get_rect(window)
        if not hwnd or not rect:
            return
        x, y, w, h = rect
        mon = _u.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not _u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return
        try:
            k = (_u.GetDpiForWindow(hwnd) or 96) / 96
        except Exception:
            k = 1.0
        wa = mi.rcWork
        snap_px, gap = round(SNAP * k), round(EDGE_GAP * k)
        # pull back inside the screen (minus the taskbar)
        nx = min(max(x, wa.left), wa.right - w)
        ny = min(max(y, wa.top), wa.bottom - h)
        # snap to edges
        if abs(nx - wa.left) <= snap_px:
            nx = wa.left + gap
        elif abs((nx + w) - wa.right) <= snap_px:
            nx = wa.right - w - gap
        if abs(ny - wa.top) <= snap_px:
            ny = wa.top + gap
        elif abs((ny + h) - wa.bottom) <= snap_px:
            ny = wa.bottom - h - gap
        if (nx, ny) != (x, y):
            _u.SetWindowPos(hwnd, None, nx, ny, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception as e:
        log.warning("snap failed: %s", e)


def place(window, saved=None):
    if sys.platform != "win32":
        return
    try:
        hwnd = _hwnd(window)
        rect = get_rect(window)
        if not hwnd or not rect:
            return
        _, _, w, h = rect
        if saved and _on_a_screen(int(saved[0]), int(saved[1]), w):
            x, y = int(saved[0]), int(saved[1])
        else:
            mon = _u.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not _u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                return
            try:
                dpi = _u.GetDpiForWindow(hwnd) or 96
            except Exception:
                dpi = 96
            m = round(MARGIN * dpi / 96)
            work = mi.rcWork                    # screen minus the taskbar
            x = work.right - w - m
            y = work.bottom - h - m
            x = max(work.left, x)
            y = max(work.top, y)
        _u.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        log.info("placed window at %d,%d (%dx%d px)", x, y, w, h)
    except Exception as e:
        log.warning("could not place window (it stays centred): %s", e)


def bring_to_front(window):
    if sys.platform != "win32":
        return
    try:
        hwnd = _hwnd(window)
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception as e:
        log.debug("bring_to_front: %s", e)


def set_topmost(window, on):
    """Always-on-top via Win32 (safe from any thread). Returns False if not on Windows."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = _hwnd(window)
        if not hwnd:
            return False
        after = wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST)
        return bool(_u.SetWindowPos(hwnd, after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))
    except Exception as e:
        log.warning("set_topmost failed: %s", e)
        return False


_drag_lock = threading.Lock()


def drag(window):
    """Move the window with the mouse until the button is released (like dragging a title bar).
    Runs in the caller's thread; returns True if a drag happened."""
    if sys.platform != "win32":
        return False
    if not _drag_lock.acquire(blocking=False):
        return False
    try:
        hwnd = _hwnd(window)
        rect = get_rect(window)
        if not hwnd or not rect:
            return False
        vk = VK_RBUTTON if _u.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        pt = wintypes.POINT()
        _u.GetCursorPos(ctypes.byref(pt))
        off_x, off_y = pt.x - rect[0], pt.y - rect[1]
        last = (rect[0], rect[1])
        started = time.monotonic()
        while _u.GetAsyncKeyState(vk) & 0x8000:
            _u.GetCursorPos(ctypes.byref(pt))
            pos = (pt.x - off_x, pt.y - off_y)
            if pos != last:
                _u.SetWindowPos(hwnd, None, pos[0], pos[1], 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                last = pos
            time.sleep(0.007)
            if time.monotonic() - started > 600:      # safety net
                break
        snap(window)
        return True
    except Exception as e:
        log.warning("drag failed: %s", e)
        return False
    finally:
        _drag_lock.release()
