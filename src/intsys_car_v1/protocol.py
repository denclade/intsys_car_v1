"""Schülerprotokoll des intsys-car-v1-Standards (spark-e/docs/intsys-car-v1-standard.md).

Ohne Hardware und ohne Netzwerk: Grenzen der Steuerwerte, Auswertung einer
Server-Antwort und Prüfung der Live-Kalibrierung. Gleiche Regeln wie SPARK-E und
mein_car_v1 (car_api.py); genutzt von Car (Fahrzeug) und CarServer (Schülerrechner).
"""
import math
import numbers

PORT = 5555

# ZeroMQ-Heartbeat (beide Seiten): Ein zmq.PAIR-Socket nimmt nur einen Partner an. Ist das
# Fahrzeug ohne Abmeldung weg (WLAN, Akku), gibt der Heartbeat die tote Verbindung nach
# HEARTBEAT_TIMEOUT_MS frei, sonst käme kein neues Fahrzeug mehr durch.
HEARTBEAT_IVL_MS = 1000
HEARTBEAT_TIMEOUT_MS = 3000
# JPEG-Qualität der Kamerabilder (SerializingSocket.send_img, Car); kleiner = weniger WLAN-Last
JPEG_QUALITY = 90

# Grenzen der Steuerwerte: Motoren in %, Winkel in Grad (Lenkung positiv = rechts,
# Neigen negativ = nach oben).
ACTUATOR_LIMITS = {
    "speed": (-100, 100),
    "left": (-100, 100),
    "right": (-100, 100),
    "steering": (-30, 30),
    "pan": (-90, 90),
    "tilt": (-65, 40),
}

# Weitere Feldnamen wie bei SPARK-E; der erste vorhandene gilt.
ACTUATOR_ALIASES = {
    "speed": ("speed", "motor", "throttle"),
    "left": ("left", "speed_left"),
    "right": ("right", "speed_right"),
    "steering": ("steering", "steer", "steering_angle", "servo_lenkung"),
    "pan": ("pan", "cam_pan", "cam_pan_angle", "servo_schwenken"),
    "tilt": ("tilt", "cam_tilt", "cam_tilt_angle", "servo_neigen"),
}

# Live-Kalibrierung: (Abschnitt in car.toml, Schlüssel) -> erlaubter Bereich.
LIVE_CALIBRATION_LIMITS = {
    ("motor_left", "min_speed_to_move"): (0, 99),
    ("motor_right", "min_speed_to_move"): (0, 99),
    ("servo_steering", "compensate_angle"): (-45, 45),
    ("servo_pan", "compensate_angle"): (-45, 45),
    ("servo_tilt", "compensate_angle"): (-45, 45),
}

# Kein Ultraschall-Echo: SPARK-E meldet 999.0, record_drive.py None.
NO_ECHO = 999.0


def actuator_value(key, value):
    """Steuerwert prüfen und auf ACTUATOR_LIMITS[key] begrenzen."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError(f"{key}: Zahl erwartet, nicht {value!r}")
    low, high = ACTUATOR_LIMITS[key]
    return max(low, min(high, float(value))) + 0.0  # + 0.0: kein "-0.0" in Ausgaben


def _stick_angle(stick, axis, negative, positive, low, high, invert=False):
    """Stick als Winkel: Ausschlag 0 … 100 je Richtung × low bzw. high.

    Alternativ eine Achse {"x": -1 … 1} (auch -100 … 100), wie bei SPARK-E.
    """
    if not isinstance(stick, dict):
        return 0.0
    neg, pos = float(stick.get(negative, 0)), float(stick.get(positive, 0))
    angle = neg / 100 * low if neg > 0 else pos / 100 * high if pos > 0 else 0.0
    if angle or axis not in stick:
        return angle
    value = float(stick[axis]) * (-1 if invert else 1)
    value = value / 100 if abs(value) > 1 else value
    return value * high if value >= 0 else -value * low


def parse_reply(reply):
    """Steuerwerte aus einer Server-Antwort, begrenzt auf ACTUATOR_LIMITS.

    Vorrang: Werte auf oberster Ebene, dann drive {speed, angle}, dann direkte Werte
    im controller, dann Controller-Daten (rt/lt, Sticks). Fehlt "controller", gilt die
    Antwort selbst als Controller-Daten. Eine leere Antwort ergibt überall 0 (anhalten).
    Ungültige Werte lösen ValueError aus.
    """
    if not isinstance(reply, dict):
        raise ValueError("Antwort muss ein Objekt sein")
    try:
        controller = reply.get("controller")
        controller = controller if isinstance(controller, dict) else reply
        drive = reply.get("drive")
        drive = drive if isinstance(drive, dict) else {}

        def first(data, key, default):
            return next((float(data[name]) for name in ACTUATOR_ALIASES[key] if name in data), default)

        if "rt" in controller or "lt" in controller:
            rt = float(controller.get("rt", 0))
            speed = rt if rt > 0 else -float(controller.get("lt", 0))
        else:
            speed = first(controller, "speed", 0.0)
        speed = first(reply, "speed", float(drive.get("speed", speed)))
        left_stick = controller.get("left_stick", {})
        right_stick = controller.get("right_stick", {})
        steering = first(controller, "steering", _stick_angle(left_stick, "x", "left", "right", -30, 30))
        if "angle" in drive:
            steering = float(drive["angle"])
        values = {
            "speed": speed,
            "left": first(reply, "left", first(controller, "left", speed)),
            "right": first(reply, "right", first(controller, "right", speed)),
            "steering": first(reply, "steering", steering),
            "pan": first(reply, "pan", first(
                controller, "pan", _stick_angle(right_stick, "x", "left", "right", -90, 90))),
            # Stick hoch = Kamera hoch = negativer Tilt
            "tilt": first(reply, "tilt", first(
                controller, "tilt", _stick_angle(right_stick, "y", "up", "down", -65, 40, invert=True))),
        }
    except (TypeError, AttributeError, OverflowError) as error:
        raise ValueError(f"Ungültige Steuerung: {error}") from None
    return {key: actuator_value(key, value) for key, value in values.items()}


def check_calibration(changes):
    """Live-Kalibrierung prüfen: Liste (Abschnitt, Schlüssel, Wert) oder ValueError.

    Alle Werte oder keiner: Ist ein Wert ungültig, wird nichts übernommen.
    """
    if not isinstance(changes, dict):
        raise ValueError("calibration muss ein Objekt sein")
    checked = []
    for section, keys in changes.items():
        if not isinstance(keys, dict):
            raise ValueError(f"Ungültige Kalibrierung: {section}")
        for key, value in keys.items():
            limits = LIVE_CALIBRATION_LIMITS.get((section, key))
            if limits is None:
                raise ValueError(f"Unbekannte Kalibrierung: {section}.{key}")
            valid = (isinstance(value, numbers.Real) and not isinstance(value, bool)
                     and limits[0] <= value <= limits[1])
            if not valid:
                raise ValueError(f"{section}.{key}: Wert muss zwischen {limits[0]} und {limits[1]} liegen")
            checked.append((section, key, float(value)))
    return checked
