"""SDL-3-Bibliothek finden und Gamepads über eine kleine ctypes-Schnittstelle lesen.

Initialisierung, poll() und close() gehören in den Hauptthread (SDL-Ereignisschleife).
"""
import ctypes
import ctypes.util
import importlib.util
import os
from pathlib import Path
import sys

from ._controller_mapping import GAMEPAD_AXES, _from_gamepad_api

SDL3_MIN_VERSION = (3, 2, 0)
# Basis-Unterstützung ist firmwareabhängig; für neue Steam Controller aktuelle SDL 3 nutzen.
SDL3_STEAM_RECOMMENDED_VERSION = (3, 4, 10)

_SDL3_NAMES = {"win32": ("SDL3.dll",), "darwin": ("libSDL3.dylib", "libSDL3.0.dylib")}
_SDL3_LINUX_NAMES = ("libSDL3.so", "libSDL3.so.0")
_SDL_INIT_GAMEPAD = 0x00002000


def _pysdl3_bin_dir():
    """Ordner, in den PySDL3 die SDL-3-Binärdateien lädt (ohne PySDL3 zu importieren)."""
    if os.environ.get("SDL_BINARY_PATH"):
        return Path(os.environ["SDL_BINARY_PATH"])
    try:
        spec = importlib.util.find_spec("sdl3")
    except (ImportError, ValueError):
        return None
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent / "bin"


def find_sdl3_library():
    """Pfad oder Name der SDL-3-Bibliothek; None, wenn keine gefunden wird."""
    explicit = os.environ.get("INTSYS_SDL3_LIBRARY")
    if explicit:
        return explicit
    names = _SDL3_NAMES.get(sys.platform, _SDL3_LINUX_NAMES)
    bin_dir = _pysdl3_bin_dir()
    if bin_dir is not None and bin_dir.is_dir():
        for name in names:
            if (bin_dir / name).exists():
                return str(bin_dir / name)
    return ctypes.util.find_library("SDL3")


class _Sdl3:
    """Die wenigen SDL-3-Funktionen für Gamepads, direkt über ctypes."""

    def __init__(self, library):
        lib = ctypes.CDLL(library)
        c_bool, c_char_p, c_int, c_uint32, c_void_p = (
            ctypes.c_bool, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p)
        signatures = {
            "SDL_GetVersion": ([], c_int),
            "SDL_GetError": ([], c_char_p),
            "SDL_SetHint": ([c_char_p, c_char_p], c_bool),
            "SDL_InitSubSystem": ([c_uint32], c_bool),
            "SDL_QuitSubSystem": ([c_uint32], None),
            "SDL_SetJoystickEventsEnabled": ([c_bool], None),
            "SDL_SetGamepadEventsEnabled": ([c_bool], None),
            "SDL_UpdateGamepads": ([], None),
            "SDL_PumpEvents": ([], None),
            "SDL_GetGamepads": ([ctypes.POINTER(c_int)], ctypes.POINTER(c_uint32)),
            "SDL_free": ([c_void_p], None),
            "SDL_OpenGamepad": ([c_uint32], c_void_p),
            "SDL_CloseGamepad": ([c_void_p], None),
            "SDL_GamepadConnected": ([c_void_p], c_bool),
            "SDL_GetGamepadName": ([c_void_p], c_char_p),
            "SDL_GetGamepadAxis": ([c_void_p, c_int], ctypes.c_int16),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(lib, name)
            function.argtypes, function.restype = argtypes, restype
            setattr(self, name[4:], function)
        self.lib = lib

    @property
    def version(self):
        number = self.GetVersion()
        return number // 1000000, number // 1000 % 1000, number % 1000

    def error(self):
        message = self.GetError()
        return message.decode("utf-8", "replace") if message else ""

    def gamepad_ids(self):
        count = ctypes.c_int(0)
        ids = self.GetGamepads(ctypes.byref(count))
        if not ids:
            return []
        try:
            return [ids[i] for i in range(count.value)]
        finally:
            self.free(ids)


class _Sdl3Backend:
    name = "sdl3"

    def __init__(self, library):
        sdl = _Sdl3(library)
        if sdl.version < SDL3_MIN_VERSION:
            raise OSError("SDL %d.%d.%d ist zu alt (mindestens 3.2)" % sdl.version)
        # Steam Controller (auch Triton) über HIDAPI; ab SDL 3.4 ohnehin Standard.
        sdl.SetHint(b"SDL_JOYSTICK_HIDAPI_STEAM", b"1")
        sdl.SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if not sdl.InitSubSystem(_SDL_INIT_GAMEPAD):
            raise OSError(sdl.error() or "SDL_InitSubSystem fehlgeschlagen")
        # Nur Zustand abfragen: ohne Ereignisse läuft keine Warteschlange voll.
        sdl.SetJoystickEventsEnabled(False)
        sdl.SetGamepadEventsEnabled(False)
        self._sdl = sdl
        self._gamepad = None
        self.device_name = None
        self.version = sdl.version

    def poll(self):
        sdl = self._sdl
        sdl.PumpEvents()
        sdl.UpdateGamepads()
        if self._gamepad is not None and not sdl.GamepadConnected(self._gamepad):
            self._close_gamepad()
        if self._gamepad is None:
            for instance_id in sdl.gamepad_ids():
                gamepad = sdl.OpenGamepad(instance_id)
                if gamepad:
                    self._gamepad = gamepad
                    name = sdl.GetGamepadName(gamepad)
                    self.device_name = name.decode("utf-8", "replace") if name else "Gamepad"
                    break
        return self._gamepad is not None

    def axes(self):
        raw = {name: self._sdl.GetGamepadAxis(self._gamepad, index) for name, index in GAMEPAD_AXES.items()}
        return _from_gamepad_api(raw)

    def _close_gamepad(self):
        self._sdl.CloseGamepad(self._gamepad)
        self._gamepad = None
        self.device_name = None

    def close(self):
        if self._gamepad is not None:
            self._close_gamepad()
        self._sdl.QuitSubSystem(_SDL_INIT_GAMEPAD)
