"""Explizite SDL-Installation und manueller Live-Test; kein Teil der Geräteabfrage."""
import importlib.util
import os
import subprocess
import sys

from ._sdl3_controller import find_sdl3_library
from .controller import BACKENDS, Gamepad

def download_sdl3(timeout=120):
    """Lädt die SDL-3-Binärdateien über PySDL3 (einmalig, braucht Internet).

    PySDL3 lädt sie beim ersten Import. Das passiert in einem eigenen Prozess: Fehlt dort
    eine Zusatzbibliothek (SDL3_ttf braucht z. B. freetype), bricht der Import ab, die für
    den Controller nötige libSDL3 liegt dann aber schon da. Gibt den Pfad zurück oder None.
    """
    if importlib.util.find_spec("sdl3") is None:
        print("PySDL3 fehlt: pip install pysdl3")
        return None
    env = dict(os.environ, SDL_DOC_GENERATOR="0", SDL_CHECK_BINARY_VERSION="0")
    try:
        subprocess.run([sys.executable, "-c", "import sdl3"], env=env, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("SDL 3 konnte nicht geladen werden:", exc)
    return find_sdl3_library()


def main(argv=None):
    """python -m intsys_car_v1.controller [--sdl3] [--backend auto|sdl3|pygame]"""
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Controller testen (Xbox, Steam Controller 2 …)")
    parser.add_argument("--sdl3", action="store_true",
                        help="SDL-3-Binärdateien über PySDL3 laden (für den Steam Controller 2 ohne Steam)")
    parser.add_argument("--backend", choices=BACKENDS, default=None)
    parser.add_argument("--once", action="store_true", help="nur Status ausgeben, nicht live lesen")
    args = parser.parse_args(argv)
    if args.sdl3:
        library = download_sdl3()
        print("SDL 3:", library or "nicht gefunden")
        if library is None:
            return 1
    backend = args.backend or ("sdl3" if args.sdl3 else None)
    pad = Gamepad(backend=backend)
    print("Backend:", pad.backend or "keins (pip install pygame oder pysdl3)")
    if pad.backend == "sdl3":
        print("SDL %d.%d.%d" % pad.version)
    if args.once or not pad.available:
        pad.update()
        print("Controller:", pad.name or "keiner")
        available = pad.available
        pad.close()
        return 0 if available else 1
    print("Live-Werte, Ende mit Strg+C")
    try:
        while True:
            data = pad.read()
            print(f"\r{pad.name or 'kein Controller':<28} {data}", end="   ", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()
    finally:
        pad.close()
    return 0
