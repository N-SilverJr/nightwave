"""
audio_worker.py — runs in its OWN process and captures what your speakers/earbuds play.

Why a separate process? The audio library (PortAudio) can hang for seconds or forever
while Windows switches devices (e.g. Bluetooth earbuds connecting). In a separate
process that can't freeze the widget: Nightwave simply kills and restarts this worker.

Shared memory layout (created by audio.py):
  [0:8]   uint64  sequence number (bumped after every audio packet)
  [8:12]  uint32  sample rate
  [12:16] int32   status  (0 starting, 1 capturing, -1 error)
  [16: ]  float32 latest FFT_SIZE mono samples
"""
import logging
import os
import sys
import threading
import time

import numpy as np

FFT_SIZE = 2048
HEADER = 16


def views(buf):
    seq = np.ndarray((1,), dtype=np.uint64, buffer=buf, offset=0)
    rate = np.ndarray((1,), dtype=np.uint32, buffer=buf, offset=8)
    status = np.ndarray((1,), dtype=np.int32, buffer=buf, offset=12)
    ring = np.ndarray((FFT_SIZE,), dtype=np.float32, buffer=buf, offset=HEADER)
    return seq, rate, status, ring


def _die_with_parent():
    """The parent holds our stdin open; when it exits (even if killed) we get EOF."""
    def watch():
        try:
            sys.stdin.read()
        except Exception:
            pass
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def main():
    shm_name, log_file = sys.argv[1], sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "real"
    logging.basicConfig(filename=log_file, level=logging.INFO,
                        format="%(asctime)s audio-worker %(levelname)s: %(message)s")
    log = logging.getLogger()
    _die_with_parent()

    from multiprocessing import shared_memory
    try:
        shm = shared_memory.SharedMemory(name=shm_name, track=False)   # Python 3.13+
    except TypeError:
        shm = shared_memory.SharedMemory(name=shm_name)
    seq, rate, status, ring = views(shm.buf)
    local = np.zeros(FFT_SIZE, np.float32)

    def push(x):
        n = len(x)
        if n == 0:
            return
        if n >= FFT_SIZE:
            local[:] = x[-FFT_SIZE:]
        else:
            local[:-n] = local[n:]
            local[-n:] = x
        ring[:] = local
        seq[0] += 1

    if mode != "real":                      # test modes (used during development)
        rate[0] = 48000
        status[0] = 1
        t = 0
        while True:
            if mode == "hang" and t > 20:
                time.sleep(3600)
            x = (0.3 * np.sin(2 * np.pi * 440 * (np.arange(512) + t * 512) / 48000)).astype(np.float32)
            push(x)
            t += 1
            time.sleep(512 / 48000)

    import pyaudiowpatch as pyaudio
    try:
        p = pyaudio.PyAudio()
        dev = p.get_default_wasapi_loopback()
        ch = max(1, int(dev["maxInputChannels"]))
        rate[0] = int(dev["defaultSampleRate"])

        def callback(in_data, frame_count, time_info, flags):
            x = np.frombuffer(in_data, dtype=np.float32)
            if ch > 1:
                x = x[: len(x) - len(x) % ch].reshape(-1, ch).mean(axis=1)
            push(x)
            return (None, pyaudio.paContinue)

        stream = p.open(format=pyaudio.paFloat32, channels=ch, rate=int(rate[0]), input=True,
                        input_device_index=dev["index"], frames_per_buffer=512,
                        stream_callback=callback)
        stream.start_stream()
        status[0] = 1
        log.info("capturing from %s @ %d Hz", dev["name"], rate[0])

        # exit when the default output changes (earbuds connected/disconnected);
        # the main app starts a fresh worker on the new device
        while stream.is_active():
            time.sleep(2)
            p2 = pyaudio.PyAudio()
            try:
                now = p2.get_default_wasapi_loopback()["name"]
            finally:
                p2.terminate()
            if now != dev["name"]:
                log.info("default output changed: %s -> %s", dev["name"], now)
                break
    except Exception as e:
        status[0] = -1
        log.warning("capture error: %s", e)
        time.sleep(1)
    os._exit(0)   # skip PortAudio cleanup: it's exactly what tends to hang


if __name__ == "__main__":
    main()
