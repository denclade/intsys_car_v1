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

## Tests

```bash
pip install -e '.[test]' && python -m pytest
```

Laufen ohne Raspberry Pi (I2C-Attrappe, gpiozero `MockFactory`).

## Build & upload package

```bash
python -m build
python3 -m twine upload --repository testpypi dist/* --verbose
python3 -m twine upload dist/*
```
