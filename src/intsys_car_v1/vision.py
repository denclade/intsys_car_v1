"""Einfache Auswertungen des Kamerabildes für Car und CarServer (und Blockly).

Arbeitet auf car.image (numpy, BGR). Ohne Bild liefern die Auswertungen 0.
"""
from pathlib import Path

import cv2
import numpy as np

# Bildbereiche: senkrechte Drittel oder das ganze Bild.
REGIONS = ("left", "center", "right", "all")

# Farbbereiche in OpenCV-HSV (H 0 … 179); Rot liegt an beiden Enden des Farbkreises.
COLORS = {
    "red": [((0, 100, 60), (10, 255, 255)), ((170, 100, 60), (179, 255, 255))],
    "orange": [((11, 100, 60), (22, 255, 255))],
    "yellow": [((23, 100, 60), (35, 255, 255))],
    "green": [((36, 80, 40), (85, 255, 255))],
    "blue": [((86, 80, 40), (130, 255, 255))],
}


def crop(image, region):
    """Senkrechtes Drittel ("left", "center", "right") oder das ganze Bild ("all")."""
    if region not in REGIONS:
        raise ValueError(f"Bereich {region!r} unbekannt, erlaubt: {', '.join(REGIONS)}")
    width = image.shape[1]
    third = width // 3
    return {
        "left": image[:, :third],
        "center": image[:, third:width - third],
        "right": image[:, width - third:],
        "all": image,
    }[region]


def brightness(image, region="all"):
    """Mittlere Helligkeit 0 (schwarz) … 255 (weiß), auf eine Nachkommastelle."""
    if image is None:
        return 0.0
    gray = cv2.cvtColor(np.ascontiguousarray(crop(image, region)), cv2.COLOR_BGR2GRAY)
    return round(float(gray.mean()), 1)


def color_share(image, color, region="all"):
    """Anteil der Pixel in der Farbe in Prozent (0 … 100), auf eine Nachkommastelle."""
    if color not in COLORS:
        raise ValueError(f"Farbe {color!r} unbekannt, erlaubt: {', '.join(COLORS)}")
    if image is None:
        return 0.0
    hsv = cv2.cvtColor(np.ascontiguousarray(crop(image, region)), cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], bool)
    for low, high in COLORS[color]:
        mask |= cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8)) > 0
    return round(float(mask.mean()) * 100, 1)


def save_image(image, folder="bilder"):
    """Bild als bild_<Nummer>.jpg im Ordner speichern (nächste freie Nummer); Pfad oder None."""
    if image is None:
        return None
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [int(path.stem[5:]) for path in folder.glob("bild_*.jpg") if path.stem[5:].isdigit()]
    path = folder / f"bild_{max(numbers, default=0) + 1}.jpg"
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Konnte {path} nicht schreiben")
    return str(path)


class ImageMixin:
    """car.brightness("left"), car.color_share("red"), car.save_image() auf car.image."""

    def brightness(self, region="all"):
        """Helligkeit 0 … 255 im Bereich "left", "center", "right" oder "all"."""
        return brightness(self.image, region)

    def color_share(self, color, region="all"):
        """Anteil einer Farbe ("red", "orange", "yellow", "green", "blue") in Prozent."""
        return color_share(self.image, color, region)

    def save_image(self, folder="bilder"):
        """Aktuelles Kamerabild als bild_<Nummer>.jpg speichern, z. B. als Trainingsdaten."""
        path = save_image(self.image, folder)
        if path:
            print("Bild gespeichert:", path)
        return path
