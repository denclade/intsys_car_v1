"""Das ganze Fahrzeug als ein Objekt: Hardware aus /etc/intsys/car.toml, Steuerwerte als
Eigenschaften, optional ferngesteuert von einem Schüler-Server (CarServer)::

    from intsys_car_v1.car import Car

    car = Car()
    car.steering = 30
    car.speed = 40
    time.sleep(5)
    car.speed = 0

Mit Server (gleiche Regeln wie SPARK-E)::

    car = Car()
    car.connect("192.168.0.10")
    while car.next_frame():          # Bild + Sensordaten senden, nach der Antwort fahren
        pass

Motoren, Servos und Sensoren bleiben als car.motor_left, car.servo_steering usw.
erreichbar. Car belegt die GPIO-Pins: nicht zusätzlich eigene Motor/HC_SR04-Objekte
für dieselben Pins anlegen.
"""
import atexit
import time
import uuid

import zmq

from .actors import Motor, Servo
from .comm_lib import SerializingContext
from .config import load_car_config
from .protocol import (
    ACTUATOR_LIMITS,
    NO_ECHO,
    PORT,
    actuator_value,
    check_calibration,
    parse_reply,
)
from .sensors import HC_SR04, AnalogGrayscale
from .vision import ImageMixin

FRAME_INTERVAL = 0.05  # höchstens 20 Durchläufe pro Sekunde
REPLY_TIMEOUT_MS = 2000  # ohne Antwort nach 2 s anhalten (Standard)
_SERVOS = {"steering": "servo_steering", "pan": "servo_pan", "tilt": "servo_tilt"}


def _actuator(key, doc):
    return property(lambda self: self._values[key], lambda self, value: self._set(key, value), doc=doc)


def _line(key):
    return property(lambda self: bool(self._measure()["linien"].get(key, False)),
                    doc=f"Liniensensor: {key} (True/False)")


def _line_value(key):
    return property(lambda self: self._measure()["linien"].get(key), doc=f"Liniensensor-Rohwert {key}")


class Car(ImageMixin):
    """PiCar-X mit Robot HAT nach dem intsys-car-v1-Standard."""

    speed = _actuator("speed", "Geschwindigkeit beider Motoren, −100 … 100")
    left = _actuator("left", "Motor links, −100 … 100")
    right = _actuator("right", "Motor rechts, −100 … 100")
    steering = _actuator("steering", "Lenkung in Grad, −30 … 30, positiv = rechts")
    pan = _actuator("pan", "Kamera schwenken in Grad, −90 … 90")
    tilt = _actuator("tilt", "Kamera neigen in Grad, −65 (oben) … 40 (unten)")

    line_left = _line("leftDetected")
    line_center = _line("centerDetected")
    line_right = _line("rightDetected")
    line_left_value = _line_value("leftValue")
    line_center_value = _line_value("centerValue")
    line_right_value = _line_value("rightValue")

    def __init__(self, config=None, factory=None):
        self.config = config if config is not None else load_car_config()
        if factory is None:
            from gpiozero.pins.lgpio import LGPIOFactory
            factory = LGPIOFactory()
        self.factory = factory
        self.motor_left = Motor(factory=factory, **self.config["motor_left"])
        self.motor_right = Motor(factory=factory, **self.config["motor_right"])
        self.servo_steering = Servo("Lenkung", **self.config["servo_steering"])
        self.servo_pan = Servo("Kamera schwenken", **self.config["servo_pan"])
        self.servo_tilt = Servo("Kamera neigen", **self.config["servo_tilt"])
        self.ultrasonic = HC_SR04(factory=factory, **self.config["ultrasonic"])
        self.line_sensor = AnalogGrayscale(**self.config["line_sensor"])

        self.reply = {}        # letzte Antwort des Servers
        self.frame_count = 0   # Anzahl Durchläufe von next_frame()
        self.sensor_errors = {}
        self._values = dict.fromkeys(ACTUATOR_LIMITS, 0.0)
        self._calibration_session = uuid.uuid4().hex
        self._calibration_error = None
        self._sensors = None
        self._sensors_time = None
        self._camera = None
        self._image = None
        self._context = None
        self._socket = None
        self._server_lost = False
        self._last_frame = None
        self._closed = False
        atexit.register(self.close)
        self.stop()

    # --- Steuerung ----------------------------------------------------------

    def stop(self):
        """Motoren aus, Lenkung und Kamera geradeaus."""
        for key in ACTUATOR_LIMITS:
            self._set(key, 0)

    def _set(self, key, value):
        value = actuator_value(key, value)
        self._values[key] = value
        if key == "speed":
            self._values["left"] = self._values["right"] = value
            self.motor_left.setSpeed(value)
            self.motor_right.setSpeed(value)
        elif key == "left":
            self.motor_left.setSpeed(value)
        elif key == "right":
            self.motor_right.setSpeed(value)
        else:
            getattr(self, _SERVOS[key]).setAngle(value)

    # --- Sensoren -----------------------------------------------------------

    @property
    def distance(self):
        """Ultraschall-Abstand in cm; 999 ohne Echo."""
        distance = self._measure()["ultraschall"]
        return NO_ECHO if distance is None else distance

    @property
    def image(self):
        """Kamerabild (numpy, BGR); startet die Kamera beim ersten Zugriff."""
        if self._image is None:
            self._image = self._capture()
        return self._image

    def _measure(self, force=False):
        """Ultraschall und Liniensensor, höchstens alle FRAME_INTERVAL Sekunden neu gemessen."""
        now = time.monotonic()
        if force or self._sensors is None or now - self._sensors_time >= FRAME_INTERVAL:
            errors = {}
            try:
                distance = self.ultrasonic.get_distance_cm()  # kein Echo: None
            except OSError as error:
                distance = None
                errors["ultrasonic"] = str(error)
            try:
                lines = self.line_sensor.get_line_detection_data()
            except OSError as error:
                lines = {}
                errors["grayscale"] = str(error)
            self._sensors = {"ultraschall": distance, "linien": lines}
            self._sensors_time = now
            self.sensor_errors = errors
        return self._sensors

    def _capture(self):
        if self._camera is None:
            from libcamera import Transform
            from picamera2 import Picamera2
            flip = self.config["camera"]
            camera = Picamera2()
            camera.configure(camera.create_video_configuration(
                main={"format": "XRGB8888", "size": (640, 480)},
                transform=Transform(hflip=flip["hflip"], vflip=flip["vflip"])))
            camera.start()
            time.sleep(2)  # Sensor aufwärmen lassen
            self._camera = camera
        # XRGB8888 liegt als B, G, R, X vor; die ersten drei Kanäle sind OpenCVs BGR.
        return self._camera.capture_array()[:, :, :3]

    # --- Server -------------------------------------------------------------

    def connect(self, server_ip, port=PORT):
        """Mit dem Schüler-Server verbinden; next_frame() tauscht dann Bild und Antwort aus."""
        self._context = SerializingContext()
        self._socket = self._context.socket(zmq.PAIR)
        self._socket.setsockopt(zmq.RCVTIMEO, REPLY_TIMEOUT_MS)
        self._socket.setsockopt(zmq.SNDTIMEO, REPLY_TIMEOUT_MS)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(f"tcp://{server_ip}:{port}")
        print(f"Verbinde mit Server {server_ip}:{port}")

    def next_frame(self):
        """Ein Durchlauf der Fahrschleife (höchstens 20 pro Sekunde).

        Misst die Sensoren neu. Mit Server: Bild + Sensordaten senden und nach der
        Antwort fahren; bleibt sie 2 s aus, hält das Fahrzeug an. False nach Strg+C.
        """
        if self._closed:
            return False
        try:
            if self._last_frame is not None:
                time.sleep(max(0.0, self._last_frame + FRAME_INTERVAL - time.monotonic()))
            self._last_frame = time.monotonic()
            self._measure(force=True)
            self._image = None
            if self._socket is not None:
                self._exchange()
        except KeyboardInterrupt:
            self.close()
            return False
        self.frame_count += 1
        return True

    def sensor_data(self):
        """Sensordaten für den Server, wie im Standard."""
        sensors = self._measure()
        data = {
            "ultraschall": sensors["ultraschall"],
            "linien": dict(sensors["linien"]),
            "actor_data": {"speed": self.speed, "steering_angle": self.steering,
                           "cam_pan_angle": self.pan, "cam_tilt_angle": self.tilt},
            "sensor_errors": dict(self.sensor_errors),
            "calibration": self._current_calibration(),
            "calibration_session": self._calibration_session,
        }
        if self._calibration_error:
            data["calibration_error"] = self._calibration_error
        return data

    def _exchange(self):
        try:
            self._socket.send_img(self.image, self.sensor_data())
            self._calibration_error = None  # einmal gemeldet
            reply = self._socket.recv_json()
        except zmq.Again:
            if not self._server_lost:
                print("Keine Antwort vom Server, Fahrzeug hält an")
            self._server_lost = True
            reply = {}
        else:
            self._server_lost = False
        self.reply = reply if isinstance(reply, dict) else {}
        if "calibration" in self.reply:
            self._apply_calibration(self.reply["calibration"])
        try:
            values = parse_reply(self.reply)
        except ValueError as error:
            print(f"{error}, Fahrzeug hält an")
            values = parse_reply({})
        for key in ("left", "right", "steering", "pan", "tilt"):
            self._set(key, values[key])
        self._values["speed"] = values["speed"]

    # --- Live-Kalibrierung --------------------------------------------------

    def _calibration_devices(self):
        return {
            ("motor_left", "min_speed_to_move"): self.motor_left,
            ("motor_right", "min_speed_to_move"): self.motor_right,
            ("servo_steering", "compensate_angle"): self.servo_steering,
            ("servo_pan", "compensate_angle"): self.servo_pan,
            ("servo_tilt", "compensate_angle"): self.servo_tilt,
        }

    def _current_calibration(self):
        current = {}
        for (section, key), device in self._calibration_devices().items():
            current.setdefault(section, {})[key] = getattr(device, key)
        return current

    def _apply_calibration(self, changes):
        """Alle Werte oder keiner; ein Fehler wird einmal an den Server gemeldet."""
        try:
            checked = check_calibration(changes)
        except ValueError as error:
            self._calibration_error = str(error)
            return
        devices = self._calibration_devices()
        for section, key, value in checked:
            setattr(devices[(section, key)], key, value)  # Motoren übernehmen das sofort
        for key, servo in _SERVOS.items():  # Servos fahren die neue Mitte an
            getattr(self, servo).setAngle(self._values[key])

    # --- Ende ---------------------------------------------------------------

    def close(self):
        """Anhalten, Servos auf 0° und alles freigeben (läuft auch bei Programmende)."""
        if self._closed:
            return
        self._closed = True
        for action in (self.stop, self.motor_left.close, self.motor_right.close,
                       self.ultrasonic.close, self.line_sensor.close):
            try:
                action()
            except Exception as error:  # beim Aufräumen weitermachen
                print(f"Fehler beim Beenden: {error}")
        if self._camera is not None:
            self._camera.stop()
        if self._socket is not None:
            self._socket.close(linger=0)
            self._context.term()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
