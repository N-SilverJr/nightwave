"""
audio.py — turns what your speakers/earbuds are playing into 64 frequency bands.

The actual capture happens in a separate process (audio_worker.py) so that a
stuck audio driver (e.g. Bluetooth earbuds connecting) can never freeze the widget.
A watchdog restarts the worker when it dies, hangs, or the output device changes.

Nothing is recorded or saved: only the last ~40 ms of sound is kept in memory.
"""
import logging
import math
import os
import subprocess
import sys
import threading
import time
from multiprocessing import shared_memory

import numpy as np

import audio_worker

log = logging.getLogger("nightwave.audio")
HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import pyaudiowpatch  # noqa: F401  (only to know whether capture is possible)
    PYAUDIO_OK, PYAUDIO_ERR = True, None
except Exception as e:
    PYAUDIO_OK, PYAUDIO_ERR = False, e


class AudioSpectrum:
    BANDS = 64
    FFT = audio_worker.FFT_SIZE
    F_LO, F_HI = 35.0, 16000.0
    DB_FLOOR, DB_RANGE = -68.0, 56.0      # -68 dB → 0, -12 dB → 1
    TILT_DB = 14.0                        # lift the highs (music has less energy up there)
    SILENCE_AFTER = 0.3                   # s without new audio → treat as silence
    STALE_RESTART = 2.5                   # s without audio while something plays → restart worker

    def __init__(self, start=True, is_playing=None, log_file=None, worker_mode="real"):
        self.ok = False
        self.error = None if PYAUDIO_OK or worker_mode != "real" else f"Audio capture unavailable: {PYAUDIO_ERR}"
        self._is_playing = is_playing or (lambda: False)
        self._log_file = log_file or os.devnull
        self._mode = worker_mode
        self._shm = shared_memory.SharedMemory(create=True, size=audio_worker.HEADER + self.FFT * 4)
        self._seq, self._rate_v, self._status, self._ring = audio_worker.views(self._shm.buf)
        self._seq[0], self._rate_v[0], self._status[0] = 0, 48000, 0
        self._ring[:] = 0
        self._proc = None
        self._last_seq, self._last_change = 0, 0.0
        self._started_at = 0.0
        self._backoff, self._next_restart = 3.0, 0.0
        self.restarts = 0
        self._rate = 48000
        self._win = np.hanning(self.FFT).astype(np.float32)
        self._smooth = np.zeros(self.BANDS, np.float32)
        self._t = time.monotonic()
        self._build_bands()
        if start and (PYAUDIO_OK or worker_mode != "real"):
            threading.Thread(target=self._watchdog, name="audio-watchdog", daemon=True).start()

    # ---------- worker management ----------
    def _spawn(self):
        exe = sys.executable
        flags = 0x08000000 if sys.platform == "win32" else 0      # CREATE_NO_WINDOW
        self._status[0] = 0
        self._proc = subprocess.Popen(
            [exe, os.path.join(HERE, "audio_worker.py"), self._shm.name, self._log_file, self._mode],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, cwd=HERE)
        self._started_at = time.monotonic()
        self._last_change = self._started_at
        log.info("audio worker started (pid %s)", self._proc.pid)

    def _kill(self):
        p, self._proc = self._proc, None
        if p is None:
            return
        try:
            p.kill()
            p.wait(timeout=2)
        except Exception:
            pass

    def _watchdog(self):
        self._spawn()
        while True:
            time.sleep(0.5)
            now = time.monotonic()
            seq = int(self._seq[0])
            if seq != self._last_seq:
                self._last_seq, self._last_change = seq, now
                self._backoff = 3.0                       # healthy again
            alive = self._proc is not None and self._proc.poll() is None
            self.ok = alive and self._status[0] == 1
            if not alive:
                # worker exited: device changed, error, or crash → start a new one
                if now >= self._next_restart:
                    self.restarts += 1
                    self._next_restart = now + 1.0
                    self._spawn()
                continue
            stale = now - self._last_change > self.STALE_RESTART
            if stale and now - self._started_at > 4 and now >= self._next_restart and self._safe_playing():
                # something is playing but we hear nothing: stuck or wrong device
                log.info("no audio for %.1fs while playing → restarting worker", now - self._last_change)
                self.restarts += 1
                self._kill()
                self._next_restart = now + self._backoff
                self._backoff = min(30.0, self._backoff * 2)
                self._spawn()

    def _safe_playing(self):
        try:
            return bool(self._is_playing())
        except Exception:
            return False

    def close(self):
        self._kill()
        try:
            self._shm.close()
            self._shm.unlink()
        except Exception:
            pass

    # ---------- band layout ----------
    def _build_bands(self):
        nyq = self._rate / 2
        hi = min(self.F_HI, nyq * 0.95)
        edges = np.geomspace(self.F_LO, hi, self.BANDS + 1)
        bins = edges * self.FFT / self._rate
        self._bands = []
        for i in range(self.BANDS):
            lo, up = bins[i], bins[i + 1]
            if up - lo < 1.0:
                self._bands.append(("interp", (lo + up) / 2))
            else:
                self._bands.append(("range", (int(math.floor(lo)), int(math.ceil(up)))))
        self._tilt = np.linspace(0, self.TILT_DB, self.BANDS).astype(np.float32)

    def feed(self, x):
        """Push mono float samples in (-1..1) directly (used by tests)."""
        n = len(x)
        if n >= self.FFT:
            self._ring[:] = x[-self.FFT:]
        else:
            self._ring[:-n] = self._ring[n:]
            self._ring[-n:] = x
        self._seq[0] += 1
        self._last_seq, self._last_change = int(self._seq[0]), time.monotonic()

    # ---------- analysis ----------
    def snapshot(self) -> dict:
        now = time.monotonic()
        seq = int(self._seq[0])
        if seq != self._last_seq:
            self._last_seq, self._last_change = seq, now
        rate = int(self._rate_v[0]) or 48000
        if rate != self._rate:
            self._rate = rate
            self._build_bands()
        if now - self._last_change > self.SILENCE_AFTER:
            target = np.zeros(self.BANDS, np.float32)
        else:
            x = self._ring.copy()
            mag = np.abs(np.fft.rfft(x * self._win)) / (self.FFT / 4)
            idx = np.arange(len(mag))
            vals = np.empty(self.BANDS, np.float32)
            for i, (kind, arg) in enumerate(self._bands):
                if kind == "interp":
                    vals[i] = np.interp(arg, idx, mag)
                else:
                    lo, hi = arg
                    vals[i] = mag[lo:max(hi, lo + 1)].max()
            db = 20 * np.log10(vals + 1e-9) + self._tilt
            target = np.clip((db - self.DB_FLOOR) / self.DB_RANGE, 0, 1).astype(np.float32)

        dt = min(0.2, now - self._t)
        self._t = now
        up = 1 - math.exp(-dt / 0.025)       # fast attack
        down = 1 - math.exp(-dt / 0.16)      # smooth release
        k = np.where(target > self._smooth, up, down).astype(np.float32)
        self._smooth += (target - self._smooth) * k
        s = self._smooth
        return {
            "ok": self.ok, "error": self.error,
            "bands": [round(float(v), 3) for v in s],
            "bass": round(float(s[:7].mean()), 3),
            "level": round(float(s.mean()), 3),
        }
