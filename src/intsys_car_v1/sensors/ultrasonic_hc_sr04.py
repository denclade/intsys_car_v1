#Bibliotheken einbinden
import gpiozero
import time

# Der HC-SR04 beendet das Echo spätestens nach ca. 38 ms (kein Objekt in Reichweite).
ECHO_TIMEOUT_SEC = 0.04


def get_speed_of_sound(temperature_celsius):
    """
    Gibt die Schallgeschwindigkeit zurück. Die Schallgeschwindigkeit ist temperaturabhängig (je heißer, desto schneller).
    Diese Funktion nutzt die lineare Näherungsformel c_Luft = (331,5 + 0,6v)m/s, wobei v die Temperatur ist und c die
    Schallgeschwindigkeit in der Luft.
    :param temperature: Temperatur in Grad Celsius
    :return: Die Schallgeschwindigkeit in der Luft bei der angegebenen Temperatur
    """
    return 331.5 + 0.6 * temperature_celsius


class HC_SR04:
    def __init__(self, trigger_pin=27, echo_pin=22, factory=None, speed_of_sound=None, temperature_c=20):
        """
        :param factory: gpiozero Pin-Factory; ohne Angabe wird eine LGPIOFactory erzeugt.
        :param speed_of_sound: Schallgeschwindigkeit in m/s; ohne Angabe aus temperature_c berechnet.
        :param temperature_c: Lufttemperatur in °C (nur ohne speed_of_sound).
        """
        if factory is None:
            # Erst hier importieren: Ein Import der Bibliothek soll keinen GPIO-Chip öffnen.
            from gpiozero.pins.lgpio import LGPIOFactory
            factory = LGPIOFactory()
        self.trigger = gpiozero.OutputDevice(trigger_pin, pin_factory=factory)
        self.echo = gpiozero.InputDevice(echo_pin, pin_factory=factory)
        self.speed_of_sound = speed_of_sound if speed_of_sound is not None else get_speed_of_sound(temperature_c)

    def get_time_of_emit(self, echo):
        """
        Gibt den Zeitpunkt an, an dem die Töne ausgegeben werden. Ab diesem Zeitpunkt wird der ECHO Pin auf Aktiv geschaltet,
        das heißt wir warten darauf.
        :param echo: Der ECHO Pin.
        :return: Zeitpunkt, an dem der ECHO Pin aktiv wird, oder None, wenn das nicht innerhalb von
        ECHO_TIMEOUT_SEC passiert (Fehler beim Aussenden des Tons).
        """
        deadline = time.perf_counter() + ECHO_TIMEOUT_SEC
        while echo.value == 0:
            if time.perf_counter() > deadline:
                return None
        return time.perf_counter()

    def get_time_echo_ends(self, echo):
        """
        Gibt den Zeitpunkt an, zu dem der Sensor die reflektierten Töne (8) empfangen hat. Wenn der Sensor innerhalb von
        38ms nicht alle Töne empfangen hat, geht er automatisch auf INAKTIVE. Das wäre ein Fehlerfall (bsp. Kein Objekt in
        Reichweite).
        :param echo: Der ECHO Pin.
        :return: Zeitpunkt, an dem alle Töne empfangen wurden, oder None nach ECHO_TIMEOUT_SEC.
        """
        deadline = time.perf_counter() + ECHO_TIMEOUT_SEC
        while echo.value == 1:
            if time.perf_counter() > deadline:
                return None
        return time.perf_counter()

    def emit_sound(self, trigger):
        """
        Gibt 8 kurze, hochfrequenten (40 kHz) Töne aus. Der Ausstoß wird eingeleitet, indem der Triggerpin für
        10µs (Mikrosekunden) auf ACTIVE geschalten wird.
        :param trigger: Der TRIGGER Pin.
        :return: nichts
        """
        trigger.off()
        time.sleep(0.001)  # Kurz Ruhe auf der Leitung
        trigger.on()
        time.sleep(0.00001)  # Warte 10 Mikrosekunden
        trigger.off()

    def get_signal_time(self):
        """
        Misst die Laufzeit des Schalls von der Ausgabe eines Tons bis zum vollständigen Empfang des
        reflektierten Tons und gibt die Zeit für eine Strecke (hin oder zurück) zurück.
        :return: Zeit für eine Strecke in Sekunden oder None, wenn keine Messung möglich war.
        """
        self.emit_sound(self.trigger)
        signal_start = self.get_time_of_emit(self.echo)
        if signal_start is None:
            return None
        signal_end = self.get_time_echo_ends(self.echo)
        if signal_end is None:
            return None
        time_passed_sec = signal_end - signal_start
        time_per_distance_sec = time_passed_sec / 2
        return time_per_distance_sec

    def get_data(self):
        time_per_distance_sec = self.get_signal_time()
        if not time_per_distance_sec:
            return None
        distance_m = time_per_distance_sec * self.speed_of_sound
        distance_cm = distance_m * 100
        return dict(distance_m=distance_m,
                    distance_cm=distance_cm,
                    time_per_distance_sec=time_per_distance_sec,
                    speed_of_sound=self.speed_of_sound)

    def get_distance_cm(self):
        time_per_distance_sec = self.get_signal_time()
        if not time_per_distance_sec:
            return None
        distance_m = time_per_distance_sec * self.speed_of_sound
        return distance_m * 100

    def close(self):
        """Gibt TRIGGER und ECHO Pin wieder frei."""
        self.trigger.close()
        self.echo.close()


def main():
    ultrasonic = HC_SR04(27, 22)

    loop(ultrasonic)

def loop(ultrasonic):
    while True:
        print("---- Neue Messung")
        result = ultrasonic.get_data()
        if not result or result["distance_cm"] == 0:
            print("Fehler in der Messung")
            time.sleep(1)
            continue
        print(f"Dauer 1-Strecke in Sekunden: {result['time_per_distance_sec']}s")
        print(f"Schallgeschwindigkeit: {result['speed_of_sound']}m/s")
        print(f"Distanz: {result['distance_cm']} cm")
        time.sleep(1)

if __name__ == '__main__':
    main()
