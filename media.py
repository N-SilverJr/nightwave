"""
media.py — talks to Windows' "Global System Media Transport Controls".

Every app that shows up in the Windows volume flyout (Spotify, YouTube in
Chrome/Edge/Firefox, Media Player, VLC, ...) registers a *media session* there.
We read the now-playing info from those sessions and send play / pause /
next / previous / seek commands back to them.
"""
import asyncio
import base64
import datetime as dt
import logging
import threading
import time

log = logging.getLogger("nightwave.media")

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import (
        Buffer,
        DataReader,
        InputStreamOptions,
    )
    WINRT_OK, WINRT_ERR = True, None
except Exception as e:  # not on Windows, or packages missing
    WINRT_OK, WINRT_ERR = False, e

TICKS_PER_SECOND = 10_000_000
POLL_SECONDS = 0.5
CALL_TIMEOUT = 2.0          # a single Windows media call may not take longer than this


async def _limit(aw, timeout=CALL_TIMEOUT):
    """Await a WinRT operation, but give up after `timeout` seconds instead of hanging forever."""
    async def run():
        return await aw
    return await asyncio.wait_for(run(), timeout)


# ---------------------------------------------------------------- helpers
def _seconds(v):
    """WinRT TimeSpan → float seconds (handles the different pywinrt versions)."""
    if v is None:
        return 0.0
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    ticks = getattr(v, "duration", None)
    if ticks is not None:
        return ticks / TICKS_PER_SECOND
    try:
        return float(v)
    except Exception:
        return 0.0


def _utc(v):
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    return None


def friendly_name(aumid: str) -> str:
    """Turn an app id like 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify' into 'Spotify'."""
    if not aumid:
        return "Unknown"
    a = aumid.lower()
    known = [
        ("spotify", "Spotify"), ("msedge", "Edge"), ("chrome", "Chrome"),
        ("308046b0af4a39cb", "Firefox"), ("firefox", "Firefox"), ("brave", "Brave"),
        ("opera", "Opera"), ("vivaldi", "Vivaldi"), ("zunemusic", "Media Player"),
        ("zunevideo", "Movies & TV"), ("vlc", "VLC"), ("applemusic", "Apple Music"),
        ("itunes", "iTunes"), ("deezer", "Deezer"), ("tidal", "TIDAL"),
        ("youtube", "YouTube Music"), ("discord", "Discord"), ("foobar", "foobar2000"),
        ("musicbee", "MusicBee"), ("aimp", "AIMP"),
    ]
    for key, name in known:
        if key in a:
            return name
    name = aumid.split("!")[-1].split("\\")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name[:1].upper() + name[1:]


def _sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"GIF":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# ---------------------------------------------------------------- controller
class MediaController:
    """Runs its own asyncio loop on a background thread and keeps `self._state` fresh."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = self._empty_state(ok=WINRT_OK,
                                        error=None if WINRT_OK else f"Windows media API unavailable: {WINRT_ERR}")
        self._manager = None
        self._session = None          # session currently shown
        self._selected_id = None      # app the user pinned; None = follow Windows
        self._art_key = None
        self._art_url = None
        self._art_id = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="media", daemon=True)
        self._thread.start()

    # ---------- public (called from the UI thread) ----------
    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._state)
        base, mono = s.pop("_pos"), s.pop("_mono")
        pos = base
        if s["playing"] and s["dur"] > 0:
            pos = base + (time.monotonic() - mono) * s.get("rate", 1.0)
        s["pos"] = max(0.0, min(pos, s["dur"])) if s["dur"] > 0 else 0.0
        s.pop("rate", None)
        return s

    def art(self):
        with self._lock:
            return self._art_url

    def select(self, app_id):
        self._selected_id = app_id or None
        self._submit(self._refresh())

    def command(self, action: str, value=None) -> bool:
        if not WINRT_OK:
            return False
        fut = self._submit(self._command(action, value))
        try:
            return bool(fut.result(timeout=4))
        except Exception as e:
            log.warning("command %s failed: %s", action, e)
            return False

    # ---------- internals ----------
    @staticmethod
    def _empty_state(ok=True, error=None):
        return {
            "ok": ok, "error": error, "active": False,
            "title": "", "artist": "", "album": "",
            "playing": False, "dur": 0.0, "rate": 1.0,
            "can_toggle": False, "can_next": False, "can_prev": False, "can_seek": False,
            "app_id": None, "app": "", "sessions": [], "selected": None,
            "art_id": 0, "_pos": 0.0, "_mono": time.monotonic(),
        }

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run(self):
        asyncio.set_event_loop(self._loop)
        if WINRT_OK:
            self._loop.create_task(self._poll_forever())
        self._loop.run_forever()

    async def _connect(self):
        while True:
            try:
                self._manager = await _limit(SessionManager.request_async(), 10)
                return
            except Exception as e:
                self._set(ok=False, error=f"Could not open media sessions: {e}")
                await asyncio.sleep(3)

    async def _poll_forever(self):
        await self._connect()
        failures = 0
        while True:
            try:
                await asyncio.wait_for(self._refresh(), 6)
                failures = 0
            except Exception as e:
                failures += 1
                log.info("refresh problem (%d): %r", failures, e)
                if failures >= 6:            # ~3+ s of trouble → start over with a fresh connection
                    log.info("reconnecting to Windows media sessions")
                    self._manager, self._session, self._art_key = None, None, None
                    await self._connect()
                    failures = 0
            await asyncio.sleep(POLL_SECONDS)

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _pick_session(self, sessions):
        if self._selected_id:
            for s in sessions:
                if s.source_app_user_model_id == self._selected_id:
                    return s
        cur = self._manager.get_current_session()
        if cur is not None:
            return cur
        return sessions[0] if sessions else None

    async def _refresh(self):
        if self._manager is None:
            return
        sessions = list(self._manager.get_sessions())
        session = self._pick_session(sessions)
        self._session = session

        listing = []
        for s in sessions:
            try:
                st = s.get_playback_info().playback_status
                playing = st == PlaybackStatus.PLAYING
            except Exception:
                playing = False
            listing.append({"id": s.source_app_user_model_id,
                            "name": friendly_name(s.source_app_user_model_id),
                            "playing": playing})

        if session is None:
            st = self._empty_state()
            st["sessions"] = listing
            st["selected"] = self._selected_id
            with self._lock:
                self._state = st
                self._art_url, self._art_key = None, None
            return

        app_id = session.source_app_user_model_id
        info = session.get_playback_info()
        playing = info.playback_status == PlaybackStatus.PLAYING
        c = info.controls
        rate = 1.0
        try:
            if info.playback_rate:
                rate = float(info.playback_rate)
        except Exception:
            pass

        title = artist = album = ""
        thumb = None
        key_keep = False
        try:
            props = await _limit(session.try_get_media_properties_async())
            title, artist, album = props.title or "", props.artist or "", props.album_title or ""
            thumb = props.thumbnail
        except Exception as e:
            log.debug("media props error: %r", e)
            with self._lock:                 # keep showing the last known song
                if self._state.get("app_id") == app_id:
                    title, artist, album = self._state["title"], self._state["artist"], self._state["album"]
                    thumb = None
            if thumb is None:
                key_keep = True

        # timeline → current position (Windows only stores the position at the
        # last update, so we add the time that has passed since then)
        dur = pos = 0.0
        try:
            tl = session.get_timeline_properties()
            start, end = _seconds(tl.start_time), _seconds(tl.end_time)
            dur = max(0.0, end - start)
            pos = _seconds(tl.position) - start
            last = _utc(tl.last_updated_time)
            if playing and last is not None and dur > 0:
                pos += max(0.0, (dt.datetime.now(dt.timezone.utc) - last).total_seconds()) * rate
        except Exception as e:
            log.debug("timeline error: %s", e)

        key = (app_id, title, artist, album)
        if key != self._art_key and not key_keep:
            url = await self._read_thumbnail(thumb)
            with self._lock:
                self._art_key, self._art_url = key, url
                self._art_id += 1

        with self._lock:
            self._state = {
                "ok": True, "error": None, "active": True,
                "title": title, "artist": artist, "album": album,
                "playing": playing, "dur": dur, "rate": rate,
                "can_toggle": bool(c.is_play_pause_toggle_enabled or c.is_play_enabled or c.is_pause_enabled),
                "can_next": bool(c.is_next_enabled),
                "can_prev": bool(c.is_previous_enabled),
                "can_seek": bool(c.is_playback_position_enabled) and dur > 0,
                "app_id": app_id, "app": friendly_name(app_id),
                "sessions": listing, "selected": self._selected_id,
                "art_id": self._art_id if self._art_url else 0,
                "_pos": pos, "_mono": time.monotonic(),
            }

    async def _read_thumbnail(self, ref):
        if ref is None:
            return None
        try:
            stream = await _limit(ref.open_read_async())
            size = int(stream.size)
            if size <= 0:
                return None
            data = None
            try:  # pywinrt 2/3: DataReader fills any writable buffer
                reader = DataReader(stream.get_input_stream_at(0))
                await _limit(reader.load_async(size))
                arr = bytearray(size)
                reader.read_bytes(arr)
                data = bytes(arr)
            except Exception:
                buf = Buffer(size)
                stream.seek(0)
                await _limit(stream.read_async(buf, size, InputStreamOptions.READ_AHEAD))
                data = bytes(memoryview(buf))
            if not data:
                return None
            return f"data:{_sniff_mime(data)};base64,{base64.b64encode(data).decode()}"
        except Exception as e:
            log.debug("thumbnail error: %s", e)
            return None

    async def _command(self, action, value):
        s = self._session
        if s is None:
            return False
        if action == "toggle":
            ok = await _limit(s.try_toggle_play_pause_async())
        elif action == "play":
            ok = await _limit(s.try_play_async())
        elif action == "pause":
            ok = await _limit(s.try_pause_async())
        elif action == "next":
            ok = await _limit(s.try_skip_next_async())
        elif action == "prev":
            ok = await _limit(s.try_skip_previous_async())
        elif action == "seek":
            start = 0.0
            try:
                start = _seconds(s.get_timeline_properties().start_time)
            except Exception:
                pass
            ok = await _limit(s.try_change_playback_position_async(int((float(value) + start) * TICKS_PER_SECOND)))
        else:
            return False
        await asyncio.sleep(0.12)
        try:
            await self._refresh()
        except Exception:
            pass
        return bool(ok)
