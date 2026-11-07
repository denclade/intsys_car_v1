"""Gemeinsame Controller-Schnittstelle für CarServer, Dashboard, Beispiele und Blockly.

read() liefert Stick-Richtungen und Trigger in Prozent (0 … 100), ohne Gerät neutrale
Werte. Beide Backends verwenden dasselbe Mapping. auto bevorzugt SDL 3, sonst pygame.
INTSYS_CONTROLLER erzwingt auto/sdl3/pygame; INTSYS_SDL3_LIBRARY setzt den Bibliothekspfad.
SDL 3 unterstützt neue Steam Controller direkt; pygame nutzt SDL 2 und Steam Input.

Gamepad im Hauptthread erstellen, lesen und schließen. Bei übergebenem pygame-Modul
verarbeitet der Aufrufer dessen Ereignisse selbst. Downloads gibt es ausschließlich
über den expliziten CLI-Aufruf: python -m intsys_car_v1.controller --sdl3.
"""
import os
import threading

from ._controller_mapping import _standard, controller_kind, neutral
from ._pygame_controller import _PygameBackend
from ._sdl3_controller import (
    SDL3_STEAM_RECOMMENDED_VERSION, _Sdl3Backend, find_sdl3_library,
)

# Vor der Initialisierung beider SDL-Versionen setzen.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
BACKENDS = ("auto", "sdl3", "pygame")


def _require_main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Gamepad muss im Hauptthread initialisiert und abgefragt werden")


class Gamepad:
    """Der erste angeschlossene Controller; ohne Controller (oder ohne Backend) alles 0.

    pygame: das bereits importierte Modul (CarServer, Dashboard). Wer es übergibt, holt die
    Ereignisse selbst ab (pygame.event.pump/get), bevor er read() aufruft. Ohne Angabe
    importiert und initialisiert Gamepad pygame bei Bedarf selbst und pumpt in read().
    backend: "auto", "sdl3" oder "pygame" (Standard: INTSYS_CONTROLLER oder "auto").
    """

    def __init__(self, pygame=None, backend=None, quiet=False):
        _require_main_thread()
        backend = (backend or os.environ.get("INTSYS_CONTROLLER") or "auto").lower()
        if backend not in BACKENDS:
            raise ValueError(f"Unbekanntes Controller-Backend {backend!r} (erlaubt: {', '.join(BACKENDS)})")
        self._quiet = quiet
        self._backend = None
        self._pygame = None
        self._own_pygame = False
        self._connected = False
        self.sdl3_error = None
        if backend in ("auto", "sdl3"):
            library = find_sdl3_library()
            if library is None:
                self.sdl3_error = "SDL 3 nicht gefunden"
            else:
                try:
                    self._backend = _Sdl3Backend(library)
                except (OSError, AttributeError) as exc:
                    self.sdl3_error = f"SDL 3 ({library}) nicht nutzbar: {exc}"
            if self._backend is None and backend == "sdl3":
                print(f"[Controller] {self.sdl3_error}")
        if self._backend is None and backend in ("auto", "pygame"):
            pygame = pygame if pygame is not None else self._import_pygame()
            if pygame is not None:
                self._pygame = pygame
                try:
                    self._backend = _PygameBackend(pygame)
                except pygame.error as error:
                    self.close()
                    if not quiet:
                        print(f"[Controller] pygame nicht nutzbar: {error}")
                if self._backend is not None and backend == "auto" and not quiet:
                    print("[Controller] SDL 2 aktiv; für neue Steam Controller ohne Steam SDL 3 installieren "
                          f"({self.sdl3_error}; pip install pysdl3, "
                          "python -m intsys_car_v1.controller --sdl3)")
        elif self._backend is not None and not quiet and self._backend.version < SDL3_STEAM_RECOMMENDED_VERSION:
            print("[Controller] SDL %d.%d.%d ist für neue Steam Controller veraltet (empfohlen: mindestens 3.4.10)"
                  % self._backend.version)

    def _import_pygame(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        try:
            import pygame
        except ImportError:
            return None
        try:
            pygame.init()
            if not pygame.display.get_init():  # headless: event.pump braucht das Display-Modul
                os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
                pygame.display.init()
        except pygame.error:
            pygame.quit()
            return None
        self._own_pygame = True
        return pygame

    @property
    def available(self):
        """Ein Backend (SDL 3 oder pygame) ist da."""
        return self._backend is not None

    @property
    def backend(self):
        """"sdl3", "pygame" oder None."""
        return self._backend.name if self._backend is not None else None

    @property
    def version(self):
        """SDL-3-Version als Tupel; None bei anderen oder fehlenden Backends."""
        return getattr(self._backend, "version", None)

    @property
    def connected(self):
        """Ein Controller ist angeschlossen (Stand der letzten Abfrage)."""
        return self._connected

    @property
    def name(self):
        return self._backend.device_name if self._connected else None

    @property
    def kind(self):
        """"steam", "xbox" oder "gamepad"; None ohne Controller."""
        return controller_kind(self.name) if self._connected else None

    def update(self):
        """An- und Abstecken erkennen."""
        _require_main_thread()
        if self._backend is None:
            self._connected = False
            return
        if self._own_pygame:
            try:
                self._pygame.event.pump()
            except self._pygame.error:
                pass
        previous = self.name
        self._connected = self._backend.poll()
        if not self._quiet and self.name != previous:
            if previous:
                print("Controller getrennt:", previous)
            if self.name:
                print("Controller verbunden:", self.name)

    def read(self):
        """Aktuelle Controller-Daten wie im Standard ({"rt", "lt", "left_stick", "right_stick"})."""
        self.update()
        if not self._connected:
            return neutral()
        axis = self._backend.axes()
        if axis is None:
            self._connected = False
            return neutral()
        return _standard(axis)

    def close(self):
        _require_main_thread()
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._connected = False
        if self._own_pygame:
            self._pygame.quit()
            self._own_pygame = False


# Bisheriger Name: liest inzwischen jeden Controller, nicht nur Xbox.
XboxController = Gamepad


def main(argv=None):
    """Kompatibler Einstieg für python -m und den manuellen Controller-Test."""
    from .controller_cli import main as run
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
