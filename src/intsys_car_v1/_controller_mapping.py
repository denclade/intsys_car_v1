"""Gemeinsame Achsen-Normalisierung für beide Controller-Backends."""
DEADZONE = 0.1

# pygame-Achsen des Xbox-360-Controllers unter Linux (siehe README von intsys-car-v1-server).
# Nur für Geräte, die SDL nicht als Gamepad kennt; sonst gilt die Belegung von SDL.
AXES = {"left_x": 0, "left_y": 1, "lt": 2, "right_x": 3, "right_y": 4, "rt": 5}

# Achsen der GameController-API (SDL 2: SDL_CONTROLLER_AXIS_*, SDL 3: SDL_GAMEPAD_AXIS_*).
# Sticks −32768 … 32767, Trigger 0 … 32767.
GAMEPAD_AXES = {"left_x": 0, "left_y": 1, "right_x": 2, "right_y": 3, "lt": 4, "rt": 5}
_AXIS_MAX = 32767.0


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


def _standard(axis):
    """Achsen (Sticks −1 … 1, Trigger 0 … 1) im Format des Standards."""
    def stick(x, y):
        left, right = _split(axis[x])
        up, down = _split(axis[y])
        return {"left": left, "right": right, "up": up, "down": down}

    def trigger(value):
        return max(0, min(100, int(value * 100)))

    return {"left_stick": stick("left_x", "left_y"), "right_stick": stick("right_x", "right_y"),
            "lt": trigger(axis["lt"]), "rt": trigger(axis["rt"])}


def _from_gamepad_api(raw):
    """Rohwerte der GameController-API (Ganzzahlen, siehe GAMEPAD_AXES) auf −1 … 1 bzw. 0 … 1."""
    return {name: max(-1.0, min(1.0, value / _AXIS_MAX)) for name, value in raw.items()}


def controller_kind(name):
    """Grobe Art des Controllers aus dem Namen: "steam", "xbox" oder "gamepad"."""
    lowered = (name or "").lower()
    if "steam" in lowered:
        return "steam"
    if "xbox" in lowered or "x-box" in lowered or "xinput" in lowered:
        return "xbox"
    return "gamepad"
