# Nightwave

A small floating now-playing widget for Windows. It works with **any app that plays media**: Spotify, YouTube in Chrome/Edge/Firefox, Windows Media Player, VLC and others. You can control playback right from the widget, and the visualiser reacts to whatever your speakers are playing.

## Features
- **Controls whatever is playing:** play/pause, next, previous, and click or drag the progress bar to seek.
- **Source switcher:** when several apps are playing, pick which one the widget follows. *Automatic* follows Windows' choice.
- **Live visualiser:** listens to your system audio (WASAPI loopback). There are three styles: **Ring**, **Skyline bars** and **Mirror wave**.
- **Themes:** Synthwave, Deep Ocean, Ember, OLED Mono, and **Album colours**, which takes its palette from the cover art.
- **Compact mode:** shrinks the widget to a slim now-playing bar.
- **Always on top:** can be toggled, and the widget remembers its size, position and settings.
- **Tray icon:** **—** (or `Esc`) hides Nightwave to the taskbar's hidden-icons area (the **^** arrow). Left-click the tray icon to show or hide the widget. Right-click it for play/pause/next/previous, *Always on top*, *Start with Windows* and **Quit**.
- **Its own icon:** Nightwave's icon replaces Python's in the taskbar, the tray and shortcuts. Launching it again while it's running just brings the open widget back.
- **Keyboard:** `Space` plays/pauses, `←` goes to the previous track, `→` skips to the next.

## Setup (Windows 10/11)
1. Install **Python 3.12** from python.org and tick **"Add python.exe to PATH"** during setup. Any version from 3.10 to 3.13 should work.
2. Unzip this folder somewhere permanent, e.g. `Documents\Nightwave`.
3. Double-click **`run.bat`**. The first run installs everything into a private `.venv` folder, which takes 1–2 minutes. After that it starts instantly.

Optional: double-click **`create-shortcut.bat`** to add a Nightwave shortcut, with its icon, to your Desktop and Start menu.

The toolbar appears when you hover over the widget. Drag the widget by its background or the song title, and resize it with the grip in the bottom-right corner.

If the widget ever ends up somewhere you can't see it, double-click **`reset-position.bat`**. If nothing appears at all, run **`debug.bat`** to see the error. The log is saved to `%APPDATA%\Nightwave\nightwave.log`.

### Manual setup (if you prefer the terminal)
```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Good to know
- **Skip and previous only work if the source supports them.** Spotify and YouTube playlists do. A single YouTube video or Shorts often don't, and those buttons are greyed out when the app says no.
- **Browsers:** YouTube in Chrome and Edge reports its media session automatically. In Firefox, check that `media.hardwaremediakeys.enabled` is on in `about:config`.
- **Privacy:** the visualiser only keeps the last ~40 ms of audio in memory to draw the bars. Nothing is recorded or saved.
- **Opening `ui/index.html` in a normal browser** runs a demo mode with fake songs, which is handy for working on the design.

## How it's built
| File | What it does |
|---|---|
| `main.py` | Creates the frameless window (pywebview) and exposes the `Api` the page calls. |
| `media.py` | Talks to Windows' media sessions (`GlobalSystemMediaTransportControlsSessionManager`) via pywinrt: now-playing info, artwork, timeline, commands. |
| `winpos.py` | Places the window using Win32 screen pixels, so display scaling can't push it off-screen. |
| `tray.py` | Tray icon and its menu (pystray). |
| `system.py` | Taskbar identity, single-instance handover, and *Start with Windows* (registry `Run` key). |
| `tools/make_icon.py` | Draws the icon. Edit and rerun it to change the design. |
| `audio.py` | Turns system audio into 64 log-spaced FFT bands, and supervises the capture worker (restarts it if it hangs or the device changes). |
| `audio_worker.py` | Captures system audio (PyAudioWPatch, WASAPI loopback) in its own process, so a stuck audio driver can never freeze the widget. |
| `ui/` | HTML/CSS/JS: the scene, the visualisers, the controls and the themes. |

The page polls `get_state()` twice a second and `get_spectrum()` about 30 times a second. Commands go through `control(action, value)`.

## Ideas for next steps
- Per-app volume slider (pycaw)
- Lyrics panel synced to the progress bar
- Global hotkeys
- Snap to screen edges and auto-hide
