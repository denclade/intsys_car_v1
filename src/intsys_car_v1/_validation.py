"""Gemeinsame Zahlenprüfung vor dem Begrenzen oder Anwenden von Hardwarewerten."""
import math
import numbers


def finite_number(value, name):
    """Reelle, endliche Zahl als float; bool und Zeichenketten sind keine Stellwerte."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError(f"{name}: endliche Zahl erwartet, nicht {value!r}")
    return float(value)
