"""Fahrzeug-Konfiguration (Pins, Richtungen, Kalibrierung) aus /etc/intsys/car.toml.

Die Datei wird zentral per Ansible (intsys-fleet-manager) auf jedes Fahrzeug
geschrieben. Die Schlüssel sind die Konstruktor-Parameter dieser Bibliothek,
ein Abschnitt lässt sich also direkt übergeben:

    car = load_car_config()
    motor_links = Motor(factory=factory, **car["motor_left"])
    lenkung = Servo("Lenkung", **car["servo_steering"])
    ultraschall = HC_SR04(factory=factory, **car["ultrasonic"])
    linie = AnalogGrayscale(**car["line_sensor"])

Fehlt die Datei, gilt STANDARD. Unbekannte Abschnitte, Schlüssel oder falsche
Typen sind ein Fehler: Ein Tippfehler soll ein Fahrzeug nicht still unkalibriert lassen.
"""
import copy
import os
import tomllib
from pathlib import Path

DEFAULT_PATH = "/etc/intsys/car.toml"

# intsys-car-v1-Standard (PiCar-X mit Robot HAT).
STANDARD = {
    "motor_left": dict(pwm_pin=13, dir_pin=23, min_speed_to_move=25, is_reversed=False),
    "motor_right": dict(pwm_pin=12, dir_pin=24, min_speed_to_move=25, is_reversed=True),
    "servo_steering": dict(channel=2, min_angle=-30, max_angle=30, compensate_angle=0, invert_angle=False),
    "servo_pan": dict(channel=0, min_angle=-90, max_angle=90, compensate_angle=0, invert_angle=True),
    "servo_tilt": dict(channel=1, min_angle=-65, max_angle=40, compensate_angle=0, invert_angle=False),
    "ultrasonic": dict(trigger_pin=27, echo_pin=22, temperature_c=20),
    "line_sensor": dict(threshold=300, channel_left=23, channel_center=22, channel_right=21),
    # Für Picamera2: libcamera.Transform(hflip=…, vflip=…), z. B. bei kopfüber montierter Kamera.
    # noir: Kameramodul ohne IR-Filter (NoIR); dann gilt die Tuning-Datei <sensor>_noir.json,
    # sonst bekommt das Bild einen grün/magenta Farbstich. Der Sensor meldet das nicht selbst.
    "camera": dict(hflip=False, vflip=False, noir=False),
}

# Kalibrierwerte dürfen Kommazahlen sein; Pins, Kanäle und Schwellen sind ganze Zahlen.
FLOAT_KEYS = {"min_speed_to_move", "min_angle", "max_angle", "compensate_angle", "temperature_c"}


def car_config_path():
    """Pfad der Konfiguration; INTSYS_CAR_CONFIG überschreibt den Standardpfad."""
    return Path(os.environ.get("INTSYS_CAR_CONFIG", DEFAULT_PATH))


def load_car_config(path=None):
    """STANDARD, überschrieben mit den Werten der Datei (falls vorhanden)."""
    path = Path(path) if path else car_config_path()
    config = copy.deepcopy(STANDARD)
    if not path.exists():
        return config
    with path.open("rb") as file:
        data = tomllib.load(file)
    for section, values in data.items():
        if section not in config or not isinstance(values, dict):
            raise ValueError(f"{path}: unbekannter Abschnitt [{section}]")
        for key, value in values.items():
            if key not in config[section]:
                raise ValueError(f"{path}: unbekannter Schlüssel {section}.{key}")
            if isinstance(config[section][key], bool):
                valid = isinstance(value, bool)
            elif key in FLOAT_KEYS:
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            else:
                valid = isinstance(value, int) and not isinstance(value, bool)
            if not valid:
                raise ValueError(f"{path}: {section}.{key} hat den falschen Typ ({value!r})")
            config[section][key] = value
    return config
