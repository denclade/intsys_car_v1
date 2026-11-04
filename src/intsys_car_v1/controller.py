"""Xbox-Controller über pygame, im Format des intsys-car-v1-Standards (Werte 0 … 100).

Erkennt An- und Abstecken im laufenden Betrieb. Genutzt von CarServer und vom Dashboard
in intsys-car-v1-server. pygame ist optional (Extra [server]).
"""
import os

# Controller-Ereignisse auch ohne aktives Fenster (Dashboard im Browser, Kamerafenster im Hintergrund).
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

DEADZONE = 0.1

# pygame-Achsen des Xbox-360-Controllers (siehe README von intsys-car-v1-server).
AXES = {"left_x": 0, "left_y": 1, "lt": 2, "right_x": 3, "right_y": 4, "rt": 5}


def neutral():
    """Controller-Daten ohne Ausschlag."""
    stick = {"left": 0, "right": 0, "up": 0, "down": 0}
    return {"left_stick": dict(stick), "right_stick": dict(stick), "lt": 0, "rt": 0}


def _split(value):
    """Achse −1 … 1 als zwei Richtungen 0 … 100; kleine Ausschläge zählen als 0."""
    if abs(value) < DEADZONE:
        return 0, 0
    percent = min(100, int(abs(value) * 100))
    return (percent, 0) if value < 0 else (0, percent)


def _trigger(value):
    """Trigger −1 (losgelassen) … 1 (gedrückt) als 0 … 100."""
    return max(0, min(100, int((value + 1) / 2 * 100)))


class XboxController:
    """Der erste angeschlossene Controller; ohne Controller (oder ohne pygame) alles 0."""

    def __init__(self, pygame=None):
        if pygame is None:
            try:
                import pygame
            except ImportError:
                pygame = None
        self._pygame = pygame
        self._joystick = None
        if pygame is not None:
            pygame.joystick.init()

    @property
    def available(self):
        """pygame ist installiert."""
        return self._pygame is not None

    @property
    def connected(self):
        """Ein Controller ist angeschlossen (Stand der letzten Abfrage)."""
        return self._joystick is not None

    @property
    def name(self):
        return self._joystick.get_name() if self._joystick is not None else None

    def update(self):
        """An- und Abstecken erkennen. Ereignisse muss vorher pygame.event.pump/get abholen."""
        if self._pygame is None:
            return
        count = self._pygame.joystick.get_count()
        if count == 0 and self._joystick is not None:
            print("Controller getrennt:", self._joystick.get_name())
            self._joystick = None
        elif count > 0 and self._joystick is None:
            self._joystick = self._pygame.joystick.Joystick(0)
            self._joystick.init()
            print("Controller verbunden:", self._joystick.get_name())

    def read(self):
        """Aktuelle Controller-Daten wie im Standard ({"rt", "lt", "left_stick", "right_stick"})."""
        self.update()
        joystick = self._joystick
        if joystick is None:
            return neutral()
        try:
            axis = {name: joystick.get_axis(index) for name, index in AXES.items()}
        except self._pygame.error:  # gerade abgesteckt
            self._joystick = None
            return neutral()

        def stick(x, y):
            left, right = _split(axis[x])
            up, down = _split(axis[y])
            return {"left": left, "right": right, "up": up, "down": down}

        return {"left_stick": stick("left_x", "left_y"), "right_stick": stick("right_x", "right_y"),
                "lt": _trigger(axis["lt"]), "rt": _trigger(axis["rt"])}
