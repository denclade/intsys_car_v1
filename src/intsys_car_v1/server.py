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
Kamerafenster, Tastatur (WASD) und Xbox-Controller laufen über pygame.
"""
import atexit

import numpy as np
import zmq

from .comm_lib import SerializingContext
from .controller import XboxController, neutral
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

TITLE = "intsys car (WASD fahren, q beenden)"
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

    def __init__(self, port=PORT, show_image=True):
        self.port = port
        self.message = {}      # Sensordaten des letzten Bildes (wie vom Fahrzeug gesendet)
        self.image = None      # letztes Kamerabild (numpy, BGR)
        self.frame_count = 0   # Anzahl empfangener Bilder
        self._values = {}      # gesetzte Steuerwerte; fehlende sendet der Server nicht
        self._calibration = {}
        self._reply_pending = False
        self._show_image = show_image
        self._window = None
        self._window_size = None
        self._xbox = None
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
        if self.message.get("calibration_error"):
            print(f"Kalibrierung abgelehnt: {self.message['calibration_error']}")
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
        if self._pygame is not None:
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
        """Xbox-Controller im Format des Standards (Werte 0 … 100); ohne Controller alles 0."""
        pygame = self._get_pygame()
        if pygame is None:
            return neutral()
        if self._xbox is None:
            self._xbox = XboxController(pygame)
        pygame.event.pump()
        return self._xbox.read()

    def drive_manually(self):
        """Mit Tastatur (WASD im Kamerafenster) oder Xbox-Controller fahren.

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
                print("pygame fehlt: kein Kamerafenster, keine Tastatur und kein Controller "
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

    def _show(self):
        """Kamerabild im pygame-Fenster zeigen; False, wenn beendet werden soll."""
        if not self._show_image:
            return True
        pygame = self._get_pygame()
        if pygame is None:
            self._show_image = False
            return True
        for event in pygame.event.get():
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_q):
                return False
        if self.image is None:
            size = (640, 480)
            surface = pygame.Surface(size)
            text = pygame.font.Font(None, 36).render("Warte auf Fahrzeug ...", True, (255, 255, 255))
            surface.blit(text, (20, 220))
        else:
            rgb = np.ascontiguousarray(self.image[:, :, 2::-1])  # OpenCV liefert BGR
            size = (rgb.shape[1], rgb.shape[0])
            surface = pygame.image.frombuffer(rgb.tobytes(), size, "RGB")
        if self._window is None or self._window_size != size:
            self._window = pygame.display.set_mode(size)
            self._window_size = size
            pygame.display.set_caption(TITLE)
        self._window.blit(surface, (0, 0))
        pygame.display.flip()
        return True
