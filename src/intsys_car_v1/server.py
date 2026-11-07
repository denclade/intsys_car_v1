"""Schüler-Server: das Fahrzeug verbindet sich, sendet Bild + Sensordaten und fährt nach
der Antwort. Alles Technische (Socket, Port, Takt, Kamerafenster) steckt in CarServer::

    from intsys_car_v1.server import CarServer

    car = CarServer()
    while car.next_frame():          # wartet auf das nächste Bild
        if car.distance < 20:
            car.speed = 0
        else:
            car.speed = 40

Steuerwerte bleiben stehen, bis sie geändert werden (wie ein Motor auf dem Fahrzeug).
Zum Bild gehört genau eine Antwort; CarServer sendet sie beim nächsten next_frame().
Kamerafenster und Tastatur (WASD) laufen über pygame, Controller (Xbox, Steam Controller 2 …)
über intsys_car_v1.controller (SDL 3 oder pygame). Neben dem Bild zeigt
eine Spalte Sensor- und Aktorwerte (nur Anzeige; abschalten mit CarServer(show_info=False)).
"""
import atexit
import time
from collections import deque

import numpy as np
import zmq

from . import panel
from .comm_lib import SerializingContext
from .protocol import (
    HEARTBEAT_IVL_MS,
    HEARTBEAT_TIMEOUT_MS,
    NO_ECHO,
    PORT,
    actuator_value,
    check_calibration,
    parse_reply,
)
from .vision import ImageMixin

TITLE = "intsys car (WASD fahren, Strg +/− Schrift, q beenden)"
_POLL_MS = 50  # Fenster bleibt bedienbar, während auf das Fahrzeug gewartet wird

# Tastatur wie im Dashboard von intsys-car-v1-server: halten = fahren, loslassen = 0.
KEY_SPEED = 100
KEY_STEERING = 30


def _actuator(key, doc):
    def get(self):
        if key in ("left", "right"):
            return self._values.get(key, self.speed)
        return self._values.get(key, 0.0)

    def set(self, value):
        if value is None:  # nicht mehr senden: das Fahrzeug nimmt 0 bzw. speed
            self._values.pop(key, None)
            return
        self._values[key] = actuator_value(key, value)
        if key == "speed":  # gilt wieder für beide Motoren
            self._values.pop("left", None)
            self._values.pop("right", None)

    return property(get, set, doc=doc)


def _line(key):
    return property(lambda self: bool(self._lines().get(key, False)),
                    doc=f"Liniensensor: {key} (True/False)")


def _line_value(key):
    return property(lambda self: self._lines().get(key), doc=f"Liniensensor-Rohwert {key} (None, wenn er fehlt)")


class CarServer(ImageMixin):
    """Server für ein Fahrzeug (zmq.PAIR auf Port 5555)."""

    speed = _actuator("speed", "Geschwindigkeit beider Motoren, −100 … 100")
    left = _actuator("left", "Motor links, −100 … 100 (sonst speed)")
    right = _actuator("right", "Motor rechts, −100 … 100 (sonst speed)")
    steering = _actuator("steering", "Lenkung in Grad, −30 … 30, positiv = rechts")
    pan = _actuator("pan", "Kamera schwenken in Grad, −90 … 90")
    tilt = _actuator("tilt", "Kamera neigen in Grad, −65 (oben) … 40 (unten)")

    line_left = _line("leftDetected")
    line_center = _line("centerDetected")
    line_right = _line("rightDetected")
    line_left_value = _line_value("leftValue")
    line_center_value = _line_value("centerValue")
    line_right_value = _line_value("rightValue")

    def __init__(self, port=PORT, show_image=True, show_info=True):
        self.port = port
        self.message = {}      # Sensordaten des letzten Bildes (wie vom Fahrzeug gesendet)
        self.image = None      # letztes Kamerabild (numpy, BGR)
        self.frame_count = 0   # Anzahl empfangener Bilder
        self._values = {}      # gesetzte Steuerwerte; fehlende sendet der Server nicht
        self._calibration = {}
        self._reply_pending = False
        self._show_image = show_image
        self._show_info = show_info  # Sensor-/Aktorwerte neben dem Kamerabild
        self._frame_times = deque(maxlen=30)
        self._received_at = None   # time.monotonic() des letzten Bildes
        self._compute_time = None  # Zeit vom Bild bis zur Antwort (Schülerprogramm)
        self._last_calibration_error = None  # (Text, Zeitpunkt), wird nur einmal gemeldet
        self._window = None
        self._image_size = None     # Bildgröße, für die das Fenster geöffnet wurde
        self._panel_scale = 1.0     # Schriftgröße der Info-Spalte (Strg +/−)
        self._panel_heights = {}    # benötigte Höhe der Spalte je Schriftgröße
        self._camera_cache = None   # skaliertes Bild, solange sich Bild und Größe nicht ändern
        self._gamepad = None
        self._pygame = None
        self._pygame_failed = False
        self._closed = False
        self._context = SerializingContext()
        self._socket = self._context.socket(zmq.PAIR)
        self._socket.setsockopt(zmq.SNDTIMEO, 500)
        self._socket.setsockopt(zmq.LINGER, 0)
        # Ohne Heartbeat blockiert ein ohne Abmeldung verschwundenes Fahrzeug den Port.
        self._socket.enable_heartbeat(HEARTBEAT_IVL_MS, HEARTBEAT_TIMEOUT_MS)
        self._socket.bind(f"tcp://*:{port}")
        atexit.register(self.close)
        print(f"Server wartet auf das Fahrzeug (Port {port}). Beenden: q im Kamerafenster oder Strg+C")

    # --- Ablauf -------------------------------------------------------------

    def next_frame(self):
        """Antwort zum letzten Bild senden und auf das nächste warten.

        True, sobald ein Bild da ist; False, wenn das Programm beendet werden soll
        (q im Kamerafenster, Fenster geschlossen, Strg+C).
        """
        if self._closed:
            return False
        try:
            if self._reply_pending:
                self._send_reply()
            while True:
                if not self._show():
                    self.close()
                    return False
                if self._socket.poll(_POLL_MS):
                    message, image = self._socket.recv_img()
                    break
        except KeyboardInterrupt:
            self.close()
            return False
        self.message = message if isinstance(message, dict) else {}
        self.image = image
        self.frame_count += 1
        self._reply_pending = True
        self._received_at = time.monotonic()
        self._frame_times.append(self._received_at)
        if self.message.get("calibration_error"):
            print(f"Kalibrierung abgelehnt: {self.message['calibration_error']}")
            self._last_calibration_error = (str(self.message["calibration_error"]), self._received_at)
        return True

    def stop(self):
        """Anhalten, Lenkung und Kamera geradeaus (alle Steuerwerte 0)."""
        self._values = {key: 0.0 for key in ("speed", "steering", "pan", "tilt")}

    def calibrate(self, section, key, value):
        """Live-Kalibrierung für die laufende Ausleihe, z. B. ("servo_steering", "compensate_angle", -8).

        Wird einmal mit der nächsten Antwort gesendet; ungültige Werte lösen ValueError aus.
        """
        check_calibration({section: {key: value}})
        self._calibration.setdefault(section, {})[key] = value

    def close(self):
        """Fahrzeug anhalten (leere Antwort) und alles freigeben."""
        if self._closed:
            return
        self._closed = True
        if self._reply_pending:
            try:
                self._socket.send_json({})  # leere Antwort = anhalten
            except zmq.ZMQError:
                pass
        self._socket.close(linger=0)
        self._context.term()
        if self._gamepad is not None:
            self._gamepad.close()
        if self._pygame is not None:
            panel.clear_fonts()
            self._pygame.quit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- Sensordaten --------------------------------------------------------

    @property
    def distance(self):
        """Ultraschall-Abstand in cm; 999 ohne Echo."""
        value = self.message.get("ultraschall")
        try:
            return NO_ECHO if value is None else float(value)
        except (TypeError, ValueError):
            return NO_ECHO

    def _lines(self):
        lines = self.message.get("linien")
        return lines if isinstance(lines, dict) else {}

    # --- Manuelle Steuerung --------------------------------------------------

    def key_pressed(self, key):
        """True, solange die Taste (z. B. "w" oder "space") im Kamerafenster gedrückt ist."""
        pygame = self._get_pygame()
        if pygame is None or self._window is None:
            return False
        pygame.event.pump()
        return bool(pygame.key.get_pressed()[pygame.key.key_code(key)])

    @property
    def controller(self):
        """Controller (Xbox, Steam Controller 2 …) im Format des Standards (Werte 0 … 100);
        ohne Controller alles 0."""
        pygame = self._get_pygame()
        if self._gamepad is None:
            from .controller import Gamepad
            self._gamepad = Gamepad(pygame)
            if not self._gamepad.available:
                print("Kein Controller-Support: pip install pygame (oder pysdl3)")
        if pygame is not None:
            pygame.event.pump()
        return self._gamepad.read()

    def drive_manually(self):
        """Mit Tastatur (WASD im Kamerafenster) oder Controller (Xbox, Steam Controller 2) fahren.

        W/S: vor/zurück mit 100, A/D: lenken 30°, loslassen = 0 (wie im Dashboard).
        Controller: rt vor, lt zurück, linker Stick lenkt, rechter Stick bewegt die Kamera.
        Gedrückte Tasten haben Vorrang vor dem Controller.
        """
        values = parse_reply({"controller": self.controller})
        forward, backward = self.key_pressed("w"), self.key_pressed("s")
        left, right = self.key_pressed("a"), self.key_pressed("d")
        if forward or backward:
            values["speed"] = KEY_SPEED if forward else -KEY_SPEED
        if left or right:
            values["steering"] = -KEY_STEERING if left else KEY_STEERING
        for key in ("speed", "steering", "pan", "tilt"):
            setattr(self, key, values[key])

    def _get_pygame(self):
        if self._pygame is None and not self._pygame_failed:
            try:
                import pygame
            except ImportError:
                print("pygame fehlt: kein Kamerafenster und keine Tastatur "
                      "(pip install pygame). Beenden mit Strg+C.")
                self._pygame_failed = True
                return None
            pygame.init()
            self._pygame = pygame
        return self._pygame

    # --- intern -------------------------------------------------------------

    def _send_reply(self):
        reply = dict(self._values)
        if self._calibration:
            reply["calibration"] = self._calibration
            self._calibration = {}
        try:
            self._socket.send_json(reply)
        except zmq.Again:
            print("Fahrzeug nimmt keine Antwort an")
        self._reply_pending = False
        if self._received_at is not None:
            self._compute_time = time.monotonic() - self._received_at

    def _show(self):
        """Kamerabild und Info-Spalte im pygame-Fenster zeigen; False, wenn beendet werden soll.

        Das Fenster ist in der Größe veränderbar: Die Spalte bleibt rechts mit fester Breite,
        das Bild füllt den Rest seitenrichtig (schwarze Balken). Strg +/−/0 ändert die
        Schriftgröße der Spalte.
        """
        if not self._show_image:
            return True
        pygame = self._get_pygame()
        if pygame is None:
            self._show_image = False
            return True
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN:
                if event.mod & (pygame.KMOD_CTRL | pygame.KMOD_GUI) and self._zoom(pygame, event.key):
                    continue
                if event.key == pygame.K_q:
                    return False
        image_size = (640, 480) if self.image is None else (self.image.shape[1], self.image.shape[0])
        if self._window is None or image_size != self._image_size:
            self._image_size = image_size  # neue Auflösung: Fenster passend zum Bild
            self._open_window(pygame, self._natural_size(pygame))
        window = pygame.display.get_surface()
        width, height = window.get_size()
        panel_width = panel.width(self._panel_scale) if self._show_info else 0
        window.fill((0, 0, 0))
        self._blit_camera(pygame, window, (width - panel_width, height))
        if self._show_info:
            info, _ = panel.draw(pygame, self._info(), height, self._panel_scale)
            window.blit(info, (width - panel_width, 0))
        pygame.display.flip()
        return True

    def _zoom(self, pygame, key):
        """Strg + / − / 0: Schriftgröße der Info-Spalte. True, wenn die Taste dazu gehört."""
        if key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            scale = self._panel_scale + panel.SCALE_STEP
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            scale = self._panel_scale - panel.SCALE_STEP
        elif key in (pygame.K_0, pygame.K_KP0):
            scale = 1.0
        else:
            return False
        self._panel_scale = panel.clamp_scale(scale)
        if self._show_info and self._window is not None:
            width, height = pygame.display.get_surface().get_size()
            needed = self._panel_needed(pygame)
            if height < needed:  # Spalte soll nie abgeschnitten werden
                self._open_window(pygame, (width, needed))
        return True

    def _panel_needed(self, pygame):
        scale = self._panel_scale
        if scale not in self._panel_heights:
            self._panel_heights[scale] = panel.needed_height(pygame, scale)
        return self._panel_heights[scale]

    def _natural_size(self, pygame):
        """Bild in Originalgröße plus Spalte, höchstens so groß wie der Bildschirm."""
        image_width, image_height = self._image_size
        panel_width = panel.width(self._panel_scale) if self._show_info else 0
        panel_height = self._panel_needed(pygame) if self._show_info else 0
        factor = 1.0
        try:
            desktop_width, desktop_height = pygame.display.get_desktop_sizes()[0]
            if desktop_width > 0 and desktop_height > 0:
                factor = min(1.0, (0.95 * desktop_width - panel_width) / image_width,
                             0.9 * desktop_height / image_height)
        except (AttributeError, IndexError, pygame.error):
            pass
        factor = max(factor, 0.25)
        return (round(image_width * factor) + panel_width,
                max(round(image_height * factor), panel_height))

    def _open_window(self, pygame, size):
        self._window = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption(TITLE)

    def _blit_camera(self, pygame, window, area):
        """Bild seitenrichtig in die Fläche links skalieren (Rest bleibt schwarz)."""
        area_width, area_height = area
        if area_width <= 0 or area_height <= 0:
            return
        if self.image is None:
            text = panel.font(pygame, panel.SANS, 22).render("Warte auf Fahrzeug …", True, panel.SECONDARY)
            window.blit(text, ((area_width - text.get_width()) // 2, (area_height - text.get_height()) // 2))
            return
        height, width = self.image.shape[:2]
        factor = min(area_width / width, area_height / height)
        size = (max(1, round(width * factor)), max(1, round(height * factor)))
        key = (id(self.image), self.frame_count, size)
        if self._camera_cache is None or self._camera_cache[0] != key:
            rgb = np.ascontiguousarray(self.image[:, :, 2::-1])  # OpenCV liefert BGR
            surface = pygame.image.frombuffer(rgb.tobytes(), (width, height), "RGB")
            if size != (width, height):
                surface = pygame.transform.smoothscale(surface, size)
            self._camera_cache = (key, surface)
        window.blit(self._camera_cache[1], ((area_width - size[0]) // 2, (area_height - size[1]) // 2))

    def _info(self):
        """Daten für die Info-Spalte (panel.draw)."""
        now = time.monotonic()
        times = self._frame_times
        fps = None
        if len(times) >= 2 and now - times[-1] <= panel.STALE_S:
            fps = (len(times) - 1) / (times[-1] - times[0]) if times[-1] > times[0] else None
        error = self._last_calibration_error
        motors = None
        if "left" in self._values or "right" in self._values:
            motors = (self.left, self.right)
        return {
            "frames": self.frame_count,
            "fps": fps,
            "since": None if self._received_at is None else now - self._received_at,
            "compute": self._compute_time,
            "waiting": self.image is None,
            "message": self.message,
            "target": {key: getattr(self, key) for key in ("speed", "steering", "pan", "tilt")},
            "motors": motors,
            "calibration_error": error[0] if error and now - error[1] < 10 else None,
        }
