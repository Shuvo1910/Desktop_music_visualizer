"""
╔══════════════════════════════════════════════════════════╗
║      AUDIO VISUALIZER — Windows                          ║
║  • Headphone / USB / Bluetooth — detect  all             ║
║  • Proper FFT bars — beat picture                        ║
║  • Fast attack, slow release (punchy + smooth)           ║
║  • Transparent background, desktop-center-bottom         ║
║  • Right-click = EXIT                                    ║
╚══════════════════════════════════════════════════════════╝

Install once:
    pip install pyaudiowpatch numpy

Run:
    python music_visualizer.py

List all loopback devices:
    python music_visualizer.py --list

Force a specific device:
    python music_visualizer.py --device 4
"""

import threading
import tkinter as tk
import numpy as np
import ctypes
import sys
import argparse
import time

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    print("pyaudiowpatch not found.\nRun: pip install pyaudiowpatch")
    sys.exit(1)

# ─────────────────────── CONFIG ───────────────────────────
BARS          = 68
WIN_WIDTH     = 620
WIN_HEIGHT    = 110
MARGIN_BOTTOM = 55

BAR_W         = 3           
BAR_GAP       = 4          
DOT_R         = 1.4
BAR_COLOR     = "#FFFFFF"
BG_COLOR      = "#000001"    

CHUNK         = 4096         
SMOOTHING_UP  = 0.60         
SMOOTHING_DN  = 0.18          
MIN_FREQ_HZ   = 40           
MAX_FREQ_HZ   = 14000         
UPDATE_MS     = 14           
# ──────────────────────────────────────────────────────────


def _make_gain_curve(n: int) -> np.ndarray:
    """
    Per-band gain multiplier — exponential curve.
    Bass  (i=0):   gain ≈ 0.18  → heavily reduced   (bass has huge natural energy)
    Mid   (i=n/2): gain ≈ 0.80  → moderate boost
    Treble(i=n-1): gain ≈ 2.20  → strong boost      (treble is naturally weak)
    This prevents bass bars from all clipping at the same height.
    """
    x    = np.linspace(0.0, 1.0, n)
    gain = 0.42 * np.exp(1.60 * x)   
    return gain.astype(np.float32)


GAIN_CURVE = _make_gain_curve(BARS)


def _log_bins(n_bars: int, fft_len: int, rate: int) -> list:
    lo    = max(1, int(MIN_FREQ_HZ * fft_len / rate))
    hi    = min(fft_len - 1, int(MAX_FREQ_HZ * fft_len / rate))
    edges = np.logspace(np.log10(lo), np.log10(hi), n_bars + 1).astype(int)
    edges = np.clip(edges, 0, fft_len - 1)
    return [(edges[i], max(edges[i] + 1, edges[i + 1])) for i in range(n_bars)]


def list_loopback_devices(p: pyaudio.PyAudio) -> list:
    devices = []
    try:
        for lb in p.get_loopback_device_info_generator():
            devices.append(lb)
    except Exception:
        pass
    return devices


def get_default_output_idx(p: pyaudio.PyAudio):
    """Returns the current Windows default output device index."""
    try:
        return p.get_default_wasapi_device(d_out=True)["index"]
    except Exception:
        return None


def pick_best_device(p: pyaudio.PyAudio, force_idx=None):
    """
    Return the WASAPI loopback capture device for the
    currently active output device.

    Matching strategy (in order):
      1. User --device N override
      2. Exact loopback analogue for current default output
      3. Built-in default WASAPI loopback lookup
      4. Name-based fallback match
      5. First available loopback (last resort)
    """
    loopbacks = list_loopback_devices(p)
    if not loopbacks:
        return None

    # 1. User override
    if force_idx is not None:
        try:
            return p.get_wasapi_loopback_analogue_by_index(force_idx)
        except Exception:
            pass
        for lb in loopbacks:
            if lb["index"] == force_idx:
                return lb
        print(f"[WARN] Device {force_idx} not in loopback list or has no loopback analogue; auto-selecting.")

    try:
        default_idx = get_default_output_idx(p)
        if default_idx is not None:
            return p.get_wasapi_loopback_analogue_by_index(default_idx)
    except Exception:
        pass

    try:
        return p.get_default_wasapi_loopback()
    except Exception:
        pass

    try:
        default_out = p.get_default_wasapi_device(d_out=True)
        default_name = default_out["name"]

        for lb in loopbacks:
            if default_name[:22] in lb["name"] or lb["name"][:22] in default_name:
                return lb

        words = [w for w in default_name.replace("(", " ").replace(")", " ").split()
                 if len(w) >= 4]
        best_score, best_lb = 0, None
        for lb in loopbacks:
            score = sum(1 for w in words if w.lower() in lb["name"].lower())
            if score > best_score:
                best_score, best_lb = score, lb
        if best_lb is not None and best_score > 0:
            return best_lb
    except Exception:
        pass

    return loopbacks[0]


# ──────────────────────────────────────────────────────────
class Visualizer:
    def __init__(self, force_device=None):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", False)
        self.root.wm_attributes("-transparentcolor", BG_COLOR)
        self.root.configure(bg=BG_COLOR)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x  = (sw - WIN_WIDTH)  // 2
        y  =  sh - WIN_HEIGHT - MARGIN_BOTTOM
        self.root.geometry(f"{WIN_WIDTH}x{WIN_HEIGHT}+{x}+{y}")

        self.root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        HWND_BOTTOM    = 1
        SWP_NOSIZE     = 0x0001
        SWP_NOMOVE     = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_BOTTOM, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        )

        self.canvas = tk.Canvas(
            self.root, width=WIN_WIDTH, height=WIN_HEIGHT,
            bg=BG_COLOR, highlightthickness=0
        )
        self.canvas.pack()
        self.root.bind("<Button-3>", lambda _: self._quit())
        self.root.bind("<Escape>",   lambda _: self._quit())

        self.heights      = np.zeros(BARS, dtype=np.float64)
        self.targets      = np.zeros(BARS, dtype=np.float64)
        self.running      = True
        self.force_device = force_device

        x = np.linspace(0.0, 1.0, BARS)
        self._sm_up = (0.35 + 0.45 * x).astype(np.float64)   
        self._sm_dn = (0.10 + 0.18 * x).astype(np.float64)  

        threading.Thread(target=self._audio_loop, daemon=True).start()
        self.root.after(UPDATE_MS, self._draw)
        self.root.mainloop()

    # ── Audio ────────────────────────────────────────────
    def _audio_loop(self):
        """
        Outer loop: restarts the stream whenever Windows switches
        the default output device (USB plugged in, BT connected, etc.)
        """
        while self.running:
            self._run_stream()
            if self.running:
                print("[INFO] Device changed — reconnecting in 1s…")
                time.sleep(1.0)

    def _run_stream(self):
        p = pyaudio.PyAudio()
        try:
            device = pick_best_device(p, self.force_device)
            if device is None:
                print("[ERROR] No WASAPI loopback device found.")
                print("  Try: python music_visualizer.py --list")
                time.sleep(3)
                return

            RATE     = int(device["defaultSampleRate"])
            CHANNELS = max(1, int(device["maxInputChannels"]))
            bins     = _log_bins(BARS, CHUNK // 2, RATE)
            window   = np.hanning(CHUNK)

            active_default = get_default_output_idx(p)

            print(f"\n✓ Listening: {device['name']}")
            print(f"  {RATE} Hz | {CHANNELS} ch | {BARS} bars")
            print("  Right-click the visualizer to exit.\n")

            stream = p.open(
                format=pyaudio.paFloat32,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                input_device_index=device["index"],
                frames_per_buffer=CHUNK,
            )

            max_h       = float(WIN_HEIGHT // 2 - 4)
            next_device_check = time.monotonic() + 1.0
            silence_since = None

            while self.running:
                raw = stream.read(CHUNK, exception_on_overflow=False)
                pcm = np.frombuffer(raw, dtype=np.float32).copy()

                if CHANNELS > 1:
                    pcm = pcm.reshape(-1, CHANNELS).mean(axis=1)

                buf = np.zeros(CHUNK, dtype=np.float32)
                n   = min(len(pcm), CHUNK)
                buf[:n] = pcm[:n]

                rms = float(np.sqrt(np.mean(buf[:n] * buf[:n]))) if n else 0.0
                now = time.monotonic()
                if rms < 1e-4:
                    if silence_since is None:
                        silence_since = now
                else:
                    silence_since = None

                spectrum = np.abs(np.fft.rfft(buf * window))[: CHUNK // 2]
                spectrum /= CHUNK

                vals = np.array([
                    0.6 * spectrum[s:e].max() + 0.4 * np.sqrt(np.mean(spectrum[s:e] ** 2))
                    for s, e in bins
                ], dtype=np.float64)

                # Per-band gain + global gain → pixel height
                vals = np.clip(vals * GAIN_CURVE * 900.0, 0.0, max_h)
                self.targets = vals

                if self.force_device is None and now >= next_device_check:
                    next_device_check = now + 1.0
                    cur = get_default_output_idx(p)
                    if cur != active_default:
                        print(f"[INFO] Default output changed ({active_default}→{cur})")
                        break 

                if self.force_device is None and silence_since is not None and now - silence_since >= 1.5:
                    print("[INFO] Loopback stream went silent; reconnecting.")
                    break

            stream.close()
        except Exception as e:
            print(f"[Audio error] {e}")
        finally:
            p.terminate()

    # ── Draw ─────────────────────────────────────────────
    def _draw(self):
        if not self.running:
            return

        diff          = self.targets - self.heights
        self.heights += np.where(diff > 0,
                                 diff * self._sm_up,
                                 diff * self._sm_dn)

        c = self.canvas
        c.delete("all")

        total = BARS * (BAR_W + BAR_GAP) - BAR_GAP
        x0    = (WIN_WIDTH - total) // 2
        cy    =  WIN_HEIGHT // 2

        for i, h in enumerate(self.heights):
            x  = x0 + i * (BAR_W + BAR_GAP)
            cx = x + BAR_W / 2.0

            if h < 1.5:
 
                c.create_oval(
                    cx - DOT_R, cy - DOT_R,
                    cx + DOT_R, cy + DOT_R,
                    fill=BAR_COLOR, outline=""
                )
            else:
                r = BAR_W / 2.0 

                c.create_rectangle(
                    x, cy - h + r,
                    x + BAR_W, cy + h - r,
                    fill=BAR_COLOR, outline=""
                )

                c.create_oval(
                    x, cy - h - r,
                    x + BAR_W, cy - h + r,
                    fill=BAR_COLOR, outline=""
                )
    
                c.create_oval(
                    x, cy + h - r,
                    x + BAR_W, cy + h + r,
                    fill=BAR_COLOR, outline=""
                )

        self.root.after(UPDATE_MS, self._draw)

    def _quit(self):
        self.running = False
        self.root.after(100, self.root.destroy)


# ─── Entry ────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Windows Audio Visualizer")
    ap.add_argument("--list",   action="store_true",
                    help="List all loopback/WASAPI devices and exit")
    ap.add_argument("--device", type=int, default=None,
                    help="Force device index (from --list)")
    args = ap.parse_args()

    if args.list:
        p   = pyaudio.PyAudio()
        lbs = list_loopback_devices(p)
        p.terminate()
        if not lbs:
            print("No loopback devices found.")
        else:
            print(f"\n{'IDX':>4}  DEVICE NAME")
            print("─" * 60)
            for d in lbs:
                print(f"  {d['index']:>2}   {d['name']}")
        sys.exit(0)

    print("=" * 52)
    print("  AUDIO VISUALIZER  —  Windows")
    print("  Right-click or Esc to EXIT")
    print("=" * 52)
    Visualizer(force_device=args.device)
