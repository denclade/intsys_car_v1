"""Info-Spalte neben dem Kamerabild von CarServer: was am Fahrzeug ankommt und was es meldet.

Nur Anzeige, nichts davon ist bedienbar. Gezeichnet mit pygame (wird übergeben, damit das
Modul ohne pygame importierbar bleibt). Die Daten kommen als dict aus CarServer._info():

    {"frames": 120, "fps": 29.8, "since": 0.03, "compute": 0.004, "waiting": False,
     "message": {...Sensordaten wie vom Fahrzeug...}, "target": {"speed": 40, ...},
     "motors": (40, 20) oder None, "calibration_error": "..." oder None}

Gestaltung: Gruppen durch Abstand und dezente Flächen statt Linien; drei Kontraststufen
(Werte hell, Bezeichnungen grau, Überschriften/Einheiten/Metadaten gedämpft); Farbe nur
für Abweichungen vom Normalzustand. Werte in JetBrains Mono, Text in Inter (beide OFL,
liegen in fonts/).

Alle Maße unten gelten für scale=1; mit scale (Strg +/− im Kamerafenster) wachsen Schrift
und Raster gemeinsam, die Schrift wird in ihrer echten Größe gerendert (scharf).
"""
from pathlib import Path

from .protocol import ACTUATOR_LIMITS, NO_ECHO

WIDTH = 300            # Breite bei scale=1
STALE_S = 1.0          # ab hier gilt das letzte Bild als veraltet
NEAR_CM = 20           # Ultraschall rot darunter (wie die Notbremse in main.py)
RANGE_CM = 200         # Balken Ultraschall
MAX_ERROR_LINES = 2

SCALE_MIN, SCALE_MAX, SCALE_STEP = 0.8, 2.0, 0.1

FONTS = Path(__file__).with_name("fonts")
SANS = FONTS / "Inter-Regular.ttf"
MONO = FONTS / "JetBrainsMono-Medium.ttf"

# Flächen
BASE = (17, 19, 23)          # #111317
CARD = (24, 26, 31)          # #181A1F
TRACK = (36, 40, 47)
BAR = (96, 105, 118)
# drei Kontraststufen
PRIMARY = (240, 246, 252)    # Messwerte
SECONDARY = (139, 148, 158)  # Bezeichnungen
TERTIARY = (110, 118, 129)   # Überschriften, Einheiten, Spaltentitel
QUIET = (72, 79, 88)         # Metadaten, fehlende Werte
# nur für Abweichungen
GREEN = (46, 160, 67)
GREEN_TEXT = (126, 231, 135)
AMBER = (210, 153, 34)
CORAL = (248, 81, 73)

# Raster (scale=1): gemeinsame Achsen für alle Gruppen
CARD_X = 10
LEFT = 22                  # Bezeichnungen
RIGHT = WIDTH - 22         # rechte Werteachse
COL_SENT = 170             # Aktoren: gesendet
COL_CAR = 226              # Aktoren: Fahrzeug
BAR_X, BAR_W = 102, 34     # Aktorbalken
ROW = 18
CARD_PAD = 8
HEADER = 16                # Überschrift über einer Fläche
GAP = 20                   # Abstand zwischen Gruppen
BOX_H = 24                 # Liniensensor-Felder

_fonts = {}


def clamp_scale(scale):
    """Schriftfaktor auf SCALE_MIN … SCALE_MAX, in Schritten von SCALE_STEP."""
    return round(max(SCALE_MIN, min(SCALE_MAX, scale)), 1)


def width(scale=1.0):
    """Breite der Spalte in Pixeln."""
    return round(WIDTH * scale)


def clear_fonts():
    """Vor pygame.quit(): geladene Schriften sind danach ungültig."""
    _fonts.clear()


def font(pygame, path, size):
    key = (path, size)
    if key not in _fonts:
        try:
            _fonts[key] = pygame.font.Font(str(path), size)
        except OSError:  # Schrift fehlt: pygame-Standardschrift
            _fonts[key] = pygame.font.Font(None, size + 4)
    return _fonts[key]


def _blend(color, background, alpha):
    return tuple(round(c * alpha + b * (1 - alpha)) for c, b in zip(color, background))


def _num(value, digits=0):
    """Zahl als Text mit echtem Minus; None/ungültig als "–"."""
    if value is None:
        return "–"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "?"
    text = f"{value:.{digits}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]
    return text.replace("-", "−")


def _float(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _ms(seconds):
    seconds = _float(seconds)
    return None if seconds is None else seconds * 1000


class _Panel:
    def __init__(self, pygame, height, scale):
        self.pg = pygame
        self.k = scale
        self.width = width(scale)
        self.surface = pygame.Surface((self.width, height))
        self.surface.fill(BASE)
        self.y = self.px(14)
        self.sans = font(pygame, SANS, self.px(13))
        self.small = font(pygame, SANS, self.px(11))
        self.header_font = font(pygame, SANS, self.px(10))
        self.mono = font(pygame, MONO, self.px(13))
        self.ascent = max(self.sans.get_ascent(), self.mono.get_ascent())

    def px(self, value):
        """Maß bei scale=1 in Pixel."""
        return round(value * self.k)

    # --- Text -----------------------------------------------------------------

    def text(self, font, text, color, x, y=None, right=False):
        """Text auf gemeinsamer Grundlinie der Zeile; right: x ist der rechte Rand (Pixel)."""
        rendered = font.render(text, True, color)
        if right:
            x -= rendered.get_width()
        y = self.y if y is None else y
        self.surface.blit(rendered, (x, y + self.ascent - font.get_ascent()))
        return rendered.get_width()

    def value(self, text, unit, axis, color=PRIMARY):
        """Messwert (Monospace, hell) mit gedämpfter Einheit, rechtsbündig an der Achse."""
        x = self.px(axis)
        missing = text in ("–", "?")
        if unit and not missing:
            x -= self.text(self.mono, unit, TERTIARY, x, right=True)
        self.text(self.mono, text, QUIET if missing else color, x, right=True)

    def label(self, text, color=SECONDARY, axis=LEFT):
        self.text(self.sans, text, color, self.px(axis))

    def tracked(self, text, color, axis):
        """Versalien mit Laufweite (~0.08 em)."""
        x = self.px(axis)
        for char in text:
            x += self.text(self.header_font, char, color, x) + max(1, self.px(1))

    # --- Gruppen --------------------------------------------------------------

    def group(self, title, meta=None, rows_height=None):
        """Überschrift und (mit rows_height bei scale=1) die Fläche darunter."""
        self.tracked(title.upper(), TERTIARY, LEFT)
        if meta:
            self.text(self.small, meta, QUIET, self.px(RIGHT), right=True)
        self.y += self.px(HEADER)
        if rows_height is not None:
            rect = (self.px(CARD_X), self.y, self.width - 2 * self.px(CARD_X),
                    self.px(rows_height + 2 * CARD_PAD))
            self.pg.draw.rect(self.surface, CARD, rect, border_radius=self.px(6))
            self.y += self.px(CARD_PAD)

    def end_group(self):
        self.y += self.px(CARD_PAD + GAP)

    def next_row(self, height=ROW):
        self.y += self.px(height)

    def row(self, label, text, unit=""):
        self.label(label)
        self.value(text, unit, RIGHT)
        self.next_row()

    # --- Balken ---------------------------------------------------------------

    def _bar_y(self):
        return self.y + self.ascent // 2 - self.px(1)

    def bar(self, axis, length, fraction, color):
        x, w, h, y = self.px(axis), self.px(length), self.px(4), self._bar_y()
        self.pg.draw.rect(self.surface, TRACK, (x, y, w, h), border_radius=self.px(2))
        fill = round(w * max(0.0, min(1.0, fraction)))
        if fill:
            self.pg.draw.rect(self.surface, color, (x, y, fill, h), border_radius=self.px(2))

    def signed_bar(self, value, marker, key):
        """Von der Mitte aus: Fläche = Fahrzeugwert, Strich = gesendeter Wert."""
        low, high = ACTUATOR_LIMITS[key]
        x, w, h, y = self.px(BAR_X), self.px(BAR_W), self.px(4), self._bar_y()
        half = w // 2
        mid = x + half

        def offset(v):
            return round(half * (min(v, high) / high if v >= 0 else -max(v, low) / low))

        self.pg.draw.rect(self.surface, TRACK, (x, y, w, h), border_radius=self.px(2))
        if value is not None and offset(value):
            dx = offset(value)
            self.pg.draw.rect(self.surface, BAR, (min(mid, mid + dx), y, abs(dx), h),
                              border_radius=self.px(2))
        self.pg.draw.line(self.surface, QUIET, (mid, y - self.px(2)), (mid, y + h + self.px(1)))
        if marker is not None:
            mx = mid + offset(marker)
            self.pg.draw.line(self.surface, PRIMARY, (mx, y - self.px(3)), (mx, y + h + self.px(2)),
                              max(1, self.px(1)))

    def dot(self, color, axis, radius=3):
        x, y, r = self.px(axis), self.y + self.ascent // 2 + self.px(1), self.px(radius)
        try:
            from pygame import gfxdraw  # kantenglatt
            gfxdraw.aacircle(self.surface, x, y, r, color)
            gfxdraw.filled_circle(self.surface, x, y, r, color)
        except ImportError:
            self.pg.draw.circle(self.surface, color, (x, y), r)


def _connection(p, info):
    p.group("Verbindung", f"{info['frames']} Bilder", 3 * ROW)
    since = info["since"]
    if info["waiting"]:
        dot, status, color = AMBER, "wartet auf Fahrzeug", SECONDARY
    elif since is not None and since > STALE_S:
        dot, status, color = CORAL, f"kein Bild seit {since:.1f} s", CORAL
    else:
        dot, status, color = GREEN, "verbunden", SECONDARY
    p.dot(dot, LEFT + 3)
    p.label(status, color, LEFT + 12)
    p.value(_num(info["fps"], 1), " fps", RIGHT)
    p.next_row()
    timing = info["message"].get("timing")
    timing = timing if isinstance(timing, dict) else {}
    p.row("Bildalter beim Senden", _num(_ms(timing.get("image_age"))), " ms")
    p.row("Rechenzeit bis Antwort", _num(_ms(info["compute"])), " ms")
    p.end_group()


def _sensors(p, message):
    timing = message.get("timing")
    age = _ms(timing.get("ultrasonic_age")) if isinstance(timing, dict) else None
    p.group("Sensoren", None if age is None else f"Ultraschall vor {_num(age)} ms", ROW + 4 + BOX_H)

    # Ultraschall
    distance = _float(message.get("ultraschall"))
    p.label("Ultraschall")
    fraction, color = 0, BAR
    if "ultraschall" not in message:
        p.value("–", "", RIGHT)
    elif distance is None or distance >= NO_ECHO:
        p.text(p.sans, "kein Echo", TERTIARY, p.px(RIGHT), right=True)
    else:
        near = distance < NEAR_CM
        color = CORAL if near else BAR
        p.value(_num(distance, 1), " cm", RIGHT, color if near else PRIMARY)
        fraction = distance / RANGE_CM
    p.bar(BAR_X, COL_SENT - BAR_X, fraction, color)
    p.next_row(ROW + 4)

    # Liniensensor: Fläche je Sensor, erkannt = grün getönt
    lines = message.get("linien")
    lines = lines if isinstance(lines, dict) else {}
    gap = p.px(6)
    left, box_h = p.px(LEFT), p.px(BOX_H)
    box_w = (p.px(RIGHT) - left - 2 * gap) // 3
    neutral = _blend(PRIMARY, CARD, 0.04)
    tint = _blend(GREEN, CARD, 0.15)
    radius = p.px(4)
    for index, (name, key) in enumerate((("L", "left"), ("M", "center"), ("R", "right"))):
        x = left + index * (box_w + gap)
        detected = lines.get(f"{key}Detected")
        rect = (x, p.y, box_w, box_h)
        if detected:
            p.pg.draw.rect(p.surface, tint, rect, border_radius=radius)
            p.pg.draw.rect(p.surface, GREEN, rect, max(1, p.px(1)), border_radius=radius)
            name_color = value_color = GREEN_TEXT
        else:
            p.pg.draw.rect(p.surface, neutral, rect, border_radius=radius)
            name_color, value_color = (TERTIARY, SECONDARY) if detected is not None else (QUIET, QUIET)
        y = p.y + (box_h - p.ascent) // 2
        p.text(p.sans, name, name_color, x + p.px(8), y)
        p.text(p.mono, _num(lines.get(f"{key}Value")), value_color, x + box_w - p.px(8), y, right=True)
    p.next_row(BOX_H)
    p.end_group()


ACTUATORS = (("Geschw.", "speed", "speed", "%", None),
             ("Lenkung", "steering", "steering_angle", "°", "servo_steering"),
             ("Schwenken", "pan", "cam_pan_angle", "°", "servo_pan"),
             ("Neigen", "tilt", "cam_tilt_angle", "°", "servo_tilt"))


def _actuators(p, info):
    message = info["message"]
    p.group("Aktoren", None, 16 + 5 * ROW)
    for title, axis in (("gesendet", COL_SENT), ("Fahrzeug", COL_CAR), ("Kalibr.", RIGHT)):
        p.text(p.small, title, TERTIARY, p.px(axis), right=True)
    p.next_row(16)

    actor_data = message.get("actor_data")
    actor_data = actor_data if isinstance(actor_data, dict) else {}
    calibration = message.get("calibration")
    calibration = calibration if isinstance(calibration, dict) else {}

    def calib(section, key):
        values = calibration.get(section)
        return values.get(key) if isinstance(values, dict) else None

    for label, key, car_key, unit, servo in ACTUATORS:
        sent = info["target"].get(key)
        car = _float(actor_data.get(car_key))
        differs = car is not None and sent is not None and abs(car - sent) > 0.5
        p.label(label)
        p.signed_bar(car, sent, key)
        p.value(_num(sent), unit, COL_SENT)
        p.value(_num(car), unit, COL_CAR, AMBER if differs else PRIMARY)
        if servo:  # Mitte des Servos
            p.value(_num(calib(servo, "compensate_angle")), unit, RIGHT)
        else:      # Anfahrschwelle links/rechts
            left = calib("motor_left", "min_speed_to_move")
            right = calib("motor_right", "min_speed_to_move")
            p.value("–" if left is None and right is None else f"{_num(left)}/{_num(right)}", "", RIGHT)
        p.next_row()
    motors = info["motors"]
    if motors is not None:  # Motoren einzeln gesetzt
        p.label("Motor L/R")
        p.value(f"{_num(motors[0])}/{_num(motors[1])}", "%", COL_SENT)
    p.next_row()  # Zeile bleibt sonst frei, damit die Höhe gleich bleibt
    p.end_group()


def _messages(p, info):
    p.group("Meldungen")
    errors = info["message"].get("sensor_errors")
    lines = [f"{name}: {text}" for name, text in errors.items()] if isinstance(errors, dict) else []
    if info["calibration_error"]:
        lines.insert(0, f"Kalibrierung: {info['calibration_error']}")
    if not lines:
        p.label("keine", QUIET)
    room = p.px(RIGHT - LEFT)
    for line in lines[:MAX_ERROR_LINES]:
        while len(line) > 1 and p.sans.size(line)[0] > room:
            line = line[:-2] + "…"
        p.label(line, CORAL)
        p.next_row()
    if len(lines) > MAX_ERROR_LINES:
        p.text(p.small, f"+ {len(lines) - MAX_ERROR_LINES} weitere", CORAL, p.px(RIGHT), right=True)


def draw(pygame, info, height=1000, scale=1.0):
    """Info-Spalte als pygame.Surface (Breite width(scale)) und die benötigte Höhe."""
    p = _Panel(pygame, height, scale)
    _connection(p, info)
    _sensors(p, info["message"])
    _actuators(p, info)
    top = p.y
    _messages(p, info)
    return p.surface, top + p.px(HEADER + MAX_ERROR_LINES * ROW + 8)


def needed_height(pygame, scale=1.0):
    """Höhe des Inhalts (unabhängig von den Daten, damit das Fenster nicht springt)."""
    empty = {"frames": 0, "fps": None, "since": None, "compute": None, "waiting": True,
             "message": {}, "target": {}, "motors": None, "calibration_error": None}
    return draw(pygame, empty, 1, scale)[1]
