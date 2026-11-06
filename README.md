# intsys car v1

This repository contains code run on our intsys cars (v1). It's primarily code to control the motors, servos and sensors.

## Fahrzeug-Konfiguration

Pins und Kalibrierung eines Autos stehen in `/etc/intsys/car.toml` (zentral per Ansible
gepflegt, anderer Pfad über `INTSYS_CAR_CONFIG`). Die Abschnitte passen direkt zu den
Konstruktoren:

```python
from gpiozero.pins.lgpio import LGPIOFactory
from intsys_car_v1 import load_car_config, Motor, Servo, HC_SR04, AnalogGrayscale

car = load_car_config()
factory = LGPIOFactory()
motor_links = Motor(factory=factory, **car["motor_left"])
lenkung = Servo("Lenkung", **car["servo_steering"])
ultraschall = HC_SR04(factory=factory, **car["ultrasonic"])
linie = AnalogGrayscale(**car["line_sensor"])
```

Fehlt die Datei, gilt der Standard (`CAR_CONFIG_STANDARD`). Unbekannte Schlüssel oder
falsche Typen lösen `ValueError` aus.

## Fahrzeug und Schüler-Server: `Car` und `CarServer`

Beide Seiten werden gleich bedient: Steuerwerte sind Eigenschaften, die Fahrschleife ist
`while car.next_frame():`. Port (5555), Takt, Sicherheitsstopp und Protokoll stecken in
den Klassen.

| Eigenschaft | Bedeutung |
|---|---|
| `speed`, `left`, `right` | Motoren −100 … 100 (`speed` setzt beide) |
| `steering`, `pan`, `tilt` | Lenkung −30 … 30°, Kamera schwenken −90 … 90°, neigen −65 … 40° |
| `distance` | Ultraschall in cm, 999 ohne Echo |
| `line_left`, `line_center`, `line_right` | Linie erkannt (`*_value`: Rohwert) |
| `frame_count`, `image` | Durchläufe/Bilder, Kamerabild (numpy, BGR) |

Werte außerhalb des Bereichs werden begrenzt, Nicht-Zahlen lösen `ValueError` aus.
`car.stop()` setzt alles auf 0, `car.calibrate("servo_steering", "compensate_angle", -8)`
kalibriert live (Fahrzeug: sofort bis Programmende; Server: mit der nächsten Antwort).

**Fahrzeug** (Hardware aus `car.toml`; `car.motor_left`, `car.servo_steering` usw. bleiben
erreichbar, aber nicht zusätzlich eigene `Motor`-Objekte für dieselben Pins anlegen):

```python
from intsys_car_v1 import Car

car = Car()
car.connect("192.168.0.10")   # optional: Schüler-Server
while car.next_frame():       # max. 30/s; mit Server: Bild + Sensordaten senden, Antwort fahren
    if car.distance < 15:
        car.speed = 0         # eigene Regeln nach der Server-Antwort
```

Ohne Antwort hält das Fahrzeug nach 2 s an. Programmende (auch Strg+C) stoppt die Motoren
und stellt die Servos auf 0°.

**Schüler-Server** (`pip install "intsys_car_v1[server]"` für pygame):

```python
from intsys_car_v1 import CarServer

car = CarServer()             # Port 5555, Kamerafenster
while car.next_frame():       # wartet auf das nächste Bild; q oder Fenster schließen beendet
    car.drive_manually()      # WASD im Fenster oder Xbox-Controller
    if car.distance < 20 and car.speed > 0:
        car.speed = 0
```

- Gesetzte Werte bleiben stehen und gehen mit jeder Antwort mit; nie gesetzte Werte werden
  nicht gesendet (das Fahrzeug nimmt 0).
- `drive_manually()`: W/S vor/zurück mit 100, A/D lenken 30°, loslassen = 0, wie im
  Dashboard von intsys-car-v1-server. Gedrückte Tasten haben Vorrang vor dem Controller.
- `key_pressed("space")`, `controller` (Rohdaten), `message` (Sensordaten des Fahrzeugs).
- `calibrate("servo_steering", "compensate_angle", -8)`: Live-Kalibrierung, einmal gesendet.

Auf beiden Seiten wertet `car.brightness("left")` (0 … 255; `"center"`, `"right"`, `"all"`)
und `car.color_share("red")` (% der Pixel; `"orange"`, `"yellow"`, `"green"`, `"blue"`) das
Kamerabild aus; `car.save_image("bilder")` speichert es als `bild_<Nummer>.jpg`.

`intsys_car_v1.protocol` enthält die Regeln ohne Hardware (`parse_reply`,
`check_calibration`, Grenzen); `Car` und SPARK-E werten Antworten gleich aus.

## Änderungen in 0.0.8

- Neu: `Car`, `CarServer`, `intsys_car_v1.protocol` und `intsys_car_v1.vision` (siehe oben). Extra `[server]`
  installiert pygame für Fenster, Tastatur und Controller.

## Änderungen in 0.0.7

- `Motor.min_speed_to_move` ist eine öffentliche Eigenschaft (0 … < 100) und wirkt bei
  laufendem Motor sofort. Damit lässt sich live kalibrieren, wie mit
  `Servo.compensate_angle`.

## Änderungen in 0.0.6

- `Motor`: Rückwärts wird mit `min_speed_to_move` gleich skaliert wie vorwärts (vorher
  maximal 50 % und Stillstand zwischen 0 und −33). Richtung wird vor der Leistung
  gesetzt. Neu: `close()`.
- `Servo`: `min_angle`/`max_angle` begrenzen den gewünschten Winkel vor
  `compensate_angle`, also symmetrisch um die kalibrierte Mitte; danach Begrenzung auf
  ±90°. Kein `print` mehr, numpy-Zahlen erlaubt.
- I2C: Bus-Sperre für alle Zugriffe; nach fünf Fehlversuchen `OSError` statt still
  `False`. Adresssuche ohne `i2cdetect`, einmal pro Prozess. Ein Logger pro Klasse.
- `AnalogGrayscale`: findet die Controller-Adresse wie die PWM, misst als eine gesperrte
  Transaktion mit Wiederholung, akzeptiert `"A0"`…`"A7"`, warnt bei Kanälen, die kein
  ADC-Eingang sind. Neu: `close()`.
- `HC_SR04`: Zeitlimit 40 ms beim Warten auf Beginn und Ende des Echos (vorher ohne
  Limit bzw. abhängig von der CPU), `perf_counter` statt `time.time`. `factory` ist
  optional und wird erst im Konstruktor erzeugt; `import intsys_car_v1` öffnet keinen
  GPIO-Chip mehr. Neu: `temperature_c`, `close()`.
- `load_car_config()` / `CAR_CONFIG_STANDARD` für `/etc/intsys/car.toml`.
- Paket: Python ≥ 3.11, `opencv-python-headless` statt `opencv-python`, `gpiozero` als
  Abhängigkeit. `lgpio` und `picamera2` kommen aus Raspberry Pi OS.

## Entwicklungsumgebung

```bash
mise trust          # einmalig: mise.toml freigeben
mise install        # installiert uv
mise run dev        # Paket editierbar + Test-Extras in .venv installieren
```

mise legt beim Betreten des Ordners `.venv` (Python 3.11 wie auf den Autos, von uv
bereitgestellt) an und aktiviert sie automatisch – `python` und `pytest` kommen dann
aus `.venv`. Voraussetzung: mise ist in der Shell aktiviert (`mise activate`).

## Tests

```bash
mise run test
# ohne mise:
pip install -e '.[test]' && python -m pytest
```

Laufen ohne Raspberry Pi (I2C-Attrappe, gpiozero `MockFactory`).

## Build & Upload (PyPI)

Voraussetzung: Entwicklungsumgebung wie oben eingerichtet (uv kommt über mise).

### API-Tokens

Die Tokens gehören **ausschließlich** in die Datei `.env` im Wurzelverzeichnis dieses
Repos. Sie ist git-ignoriert; eingecheckt wird nur die Vorlage `.env.template`.

```bash
cp .env.template .env
```

Dann in `.env` eintragen:

| Variable         | Für                     | Token erstellen                                   |
| ---------------- | ----------------------- | ------------------------------------------------- |
| `PYPI_TOKEN`     | `mise run publish`      | https://pypi.org/manage/account/token/            |
| `TESTPYPI_TOKEN` | `mise run publish:test` | https://test.pypi.org/manage/account/token/       |

Tokens beginnen mit `pypi-…`. TestPyPI ist ein eigener Dienst mit eigenem Account und
eigenem Token. Am besten das Token auf das Projekt `intsys_car_v1` beschränken.
mise lädt `.env` automatisch und blendet die Werte in seinen Ausgaben aus.

### Ablauf

1. `version` in `pyproject.toml` erhöhen (eine Version lässt sich nur einmal hochladen).
2. Bauen und prüfen:
   ```bash
   mise run build          # dist/*.whl + dist/*.tar.gz, danach twine check
   ```
3. Erst auf TestPyPI, dann ausprobieren:
   ```bash
   mise run publish:test
   pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ intsys_car_v1
   ```
4. Auf PyPI:
   ```bash
   mise run publish
   ```

`publish` und `publish:test` bauen vorher immer frisch (`clean` → `build`). Alle Tasks:
`mise tasks`.

Die Fahrschleife hat einen Zieltakt von 30 Hz; die Kamera wird mit 640 × 480
und 30 FPS konfiguriert (OV5647 unterstützt diesen Modus). Bei Serverbetrieb
begrenzen auch Bildverarbeitung und Antwortzeit den tatsächlich erreichten Takt.
Der Liniensensor wird pro Durchlauf gelesen. Ultraschall misst unabhängig
in einem Hintergrundthread mit mindestens 70 ms zwischen Messstarts; verwendet
wird der letzte abgeschlossene Messwert. `Car()` wartet beim Start kurz auf die
erste Messung. Ist ein Messwert älter als 0,5 s (oder fehlt er), gilt der Abstand
als unbekannt: `None` im Protokoll, `999` über `car.distance`, Grund in
`car.sensor_errors["ultrasonic"]`.

Jedes Bild trägt ab 0.0.10 `timing` für Latenzanzeigen (wie der SPARK-E-Agent):
`sent` (Sendezeit, Unix-Sekunden), `image_age` (Belichtungsbeginn bis Senden) und
`ultrasonic_age` (Ende der Messung bis Senden), beide in Sekunden auf dem Fahrzeug
gemessen; unbekannt ist `None`.

Verbindung (ab 0.0.10): `Car` und `CarServer` setzen einen ZeroMQ-Heartbeat (1 s,
Zeitlimit 3 s). Ein ohne Abmeldung verschwundenes Fahrzeug blockiert den
`zmq.PAIR`-Port des Servers dann nur einige Sekunden statt bis zum Neustart.
Liegen beim Fahrzeug mehrere Antworten bereit, nimmt `Car` die neueste.
`Car(jpeg_quality=90)` legt die JPEG-Qualität der gesendeten Bilder fest
(kleiner = weniger WLAN-Last).
