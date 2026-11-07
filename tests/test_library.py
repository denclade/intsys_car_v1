"""Runs without a Raspberry Pi: I2C is a fake bus, GPIO is gpiozero's MockFactory."""
import logging
import time

import pytest
from gpiozero.pins.mock import MockFactory

from intsys_car_v1.robot_hat import i2c


class FakeBus:
    def __init__(self, bus=1):
        self.writes = []
        self.reads = iter(())
        self.fail = False

    def _check(self):
        if self.fail:
            raise OSError("bus error")

    def write_word_data(self, address, register, value):
        self._check()
        self.writes.append((register, value))

    def write_byte_data(self, address, register, value):
        self._check()

    def read_byte(self, address):
        self._check()
        return next(self.reads, 0)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_bus(monkeypatch):
    monkeypatch.setattr(i2c, "SMBus", FakeBus)
    i2c._detected_addresses.clear()


@pytest.fixture
def factory():
    factory = MockFactory()
    yield factory
    factory.close()


def make_motor(factory, **kwargs):
    from intsys_car_v1 import Motor
    motor = Motor(13, 23, factory, **kwargs)
    events = []
    motor.pwm.pulse_width_percent = lambda value: events.append(("pwm", round(value, 3)))
    on, off = motor.dir.on, motor.dir.off
    motor.dir.on = lambda: (events.append(("dir", "reverse")), on())
    motor.dir.off = lambda: (events.append(("dir", "forward")), off())
    return motor, events


@pytest.mark.parametrize("speed, pwm", [(100, 100), (-100, 100), (50, 62.5), (-50, 62.5), (-20, 40), (1, 25.75)])
def test_motor_scaling_is_symmetric(factory, speed, pwm):
    motor, events = make_motor(factory, min_speed_to_move=25)
    motor.setSpeed(speed)
    assert events[-1] == ("pwm", pwm)


def test_motor_switches_direction_before_power(factory):
    motor, events = make_motor(factory, min_speed_to_move=25)
    motor.setSpeed(-80)
    assert events == [("dir", "reverse"), ("pwm", 85.0)]
    events.clear()
    motor.setSpeed(0)
    assert events == [("pwm", 0)]


def test_motor_live_calibration_applies_immediately(factory):
    motor, events = make_motor(factory, min_speed_to_move=25)
    motor.setSpeed(50)
    motor.min_speed_to_move = 40
    assert motor.min_speed_to_move == 40 and events[-1] == ("pwm", 70.0)
    with pytest.raises(ValueError):
        motor.min_speed_to_move = 100


def test_mounted_reversed_motor_flips_direction(factory):
    from intsys_car_v1 import Motor
    motor = Motor(12, 24, factory, is_reversed=True)
    motor.setSpeed(30)
    assert motor.dir.value == 1


def servo_angles(servo):
    angles = []
    servo.pulse_width_time = lambda pw: angles.append(round((pw - 500) / 2000 * 180 - 90, 3))
    return angles


def test_servo_limits_are_symmetric_around_calibrated_centre():
    from intsys_car_v1 import Servo
    servo = Servo("Lenkung", 2, min_angle=-30, max_angle=30, compensate_angle=-10)
    angles = servo_angles(servo)
    for angle in (-90, 0, 90, 15):
        servo.setAngle(angle)
    assert angles == [-40, -10, 20, 5]


def test_servo_inverted_and_physical_limit():
    numpy = pytest.importorskip("numpy")
    from intsys_car_v1 import Servo
    servo = Servo("Kamera schwenken", 0, invert_angle=True, compensate_angle=5)
    angles = servo_angles(servo)
    servo.setAngle(numpy.float64(20))
    servo.setAngle(numpy.int64(90))
    assert angles == [-25, -90]
    with pytest.raises(ValueError):
        servo.setAngle("10")


def test_servo_init_keeps_pulses_of_running_servos_valid(monkeypatch):
    """Simulates timer 0 of the Robot HAT MCU while three servos are created.

    The pulse registers still hold the angles of the previous run; no write may
    turn them into pulses outside 500..2500 us (the servos jerk wildly then).
    """
    from intsys_car_v1 import Servo
    from intsys_car_v1.robot_hat import pwm

    clock = Servo.CLOCK
    timer = {"psc": 352, "arr": 4095}
    previous = {0: 102, 1: 307, 2: 511}  # pulse registers: -90, 0, 90 degrees
    pulses = dict(previous)
    worst = []

    class McuBus(FakeBus):
        def write_word_data(self, address, register, value):
            value = ((value & 0xFF) << 8) | (value >> 8)  # PWM sends high byte first
            if register == pwm.PWM.REG_PSC:
                timer["psc"] = value + 1
            elif register == pwm.PWM.REG_ARR:
                timer["arr"] = value
            elif pwm.PWM.REG_CHN <= register < pwm.PWM.REG_CHN + 4:
                pulses[register - pwm.PWM.REG_CHN] = value
            worst.extend(pulse * timer["psc"] / clock * 1e6 for pulse in pulses.values())

    monkeypatch.setattr(i2c, "SMBus", McuBus)
    for name, channel in (("Lenkung", 2), ("Kamera schwenken", 0), ("Kamera neigen", 1)):
        servo = Servo(name, channel)
    assert worst and all(495 <= us <= 2505 for us in worst)  # register rounding
    assert pulses == previous  # creating a servo does not move it
    assert servo.prescaler() == 352 and servo.period() == 4095


def test_bus_errors_are_raised_not_swallowed():
    from intsys_car_v1.robot_hat import PWM
    pwm = PWM(13)
    pwm._smbus.fail = True
    with pytest.raises(OSError):
        pwm.pulse_width_percent(0)


def test_line_sensor_reads_one_locked_transaction_per_channel():
    from intsys_car_v1 import AnalogGrayscale
    sensor = AnalogGrayscale(channel_left="A3", channel_center=22, channel_right="a2")
    sensor._smbus.reads = iter([4, 0, 0, 200, 1, 0])
    data = sensor.get_line_detection_data()
    assert [w[0] for w in sensor._smbus.writes] == [20, 22, 21]
    assert (data["leftValue"], data["centerValue"], data["rightValue"]) == (1024, 200, 256)
    assert data["centerDetected"] and not data["leftDetected"]


def test_line_sensor_warns_about_non_adc_channels(caplog):
    from intsys_car_v1 import AnalogGrayscale
    with caplog.at_level(logging.WARNING):
        AnalogGrayscale(channel_left=10, channel_center=14, channel_right=20)
    assert "Kanal 10" in caplog.text and "Kanal 14" in caplog.text and "Kanal 20" not in caplog.text
    with pytest.raises(ValueError):
        AnalogGrayscale(channel_left="A8")


def test_ultrasonic_without_echo_times_out(factory):
    from intsys_car_v1 import HC_SR04
    sensor = HC_SR04(27, 22, factory, temperature_c=20)
    assert sensor.speed_of_sound == pytest.approx(343.5)
    start = time.perf_counter()
    assert sensor.get_distance_cm() is None
    assert time.perf_counter() - start < 0.5
    sensor.close()
    assert sensor.trigger.closed and sensor.echo.closed


def test_car_config(tmp_path):
    from intsys_car_v1 import CAR_CONFIG_STANDARD, load_car_config
    assert load_car_config(tmp_path / "missing.toml") == CAR_CONFIG_STANDARD
    path = tmp_path / "car.toml"
    path.write_text("[servo_steering]\ncompensate_angle = -7.5\n\n[line_sensor]\nchannel_left = 20\n")
    config = load_car_config(path)
    assert config["servo_steering"]["compensate_angle"] == -7.5
    assert config["servo_steering"]["channel"] == 2
    assert CAR_CONFIG_STANDARD["line_sensor"]["channel_left"] == 23
    path.write_text("[camera]\nnoir = true\n")
    assert load_car_config(path)["camera"] == {"hflip": False, "vflip": False, "noir": True}
    for content in ("[servo_lenkung]\nchannel = 2\n", "[motor_left]\npwm_pin = 13.5\n",
                    "[motor_left]\nis_reversed = 1\n", "[servo_steering]\ncompensate = 1\n",
                    "[camera]\nnoir = 1\n"):
        path.write_text(content)
        with pytest.raises(ValueError):
            load_car_config(path)


def test_config_sections_fit_the_constructors(factory, tmp_path):
    from intsys_car_v1 import AnalogGrayscale, HC_SR04, Motor, Servo, load_car_config
    car = load_car_config(tmp_path / "missing.toml")
    Motor(factory=factory, **car["motor_left"])
    Motor(factory=factory, **car["motor_right"])
    for section in ("servo_steering", "servo_pan", "servo_tilt"):
        Servo(section, **car[section])
    HC_SR04(factory=factory, **car["ultrasonic"])
    AnalogGrayscale(**car["line_sensor"])


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, '10'])
def test_invalid_actuator_values_do_not_reach_pwm(factory, value):
    from intsys_car_v1 import Servo
    motor, events = make_motor(factory)
    servo = Servo('Lenkung', 2)
    angles = []
    servo.pulse_width = angles.append
    with pytest.raises(ValueError):
        motor.setSpeed(value)
    with pytest.raises(ValueError):
        servo.setAngle(value)
    with pytest.raises(ValueError):
        servo.pulse_width_time(value)
    assert events == [] and angles == [] and motor.speed == 0


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_nonfinite_configuration_is_rejected(tmp_path, value):
    from intsys_car_v1 import load_car_config
    path = tmp_path / 'car.toml'
    path.write_text(f'[motor_left]\nmin_speed_to_move = {value}\n')
    with pytest.raises(ValueError):
        load_car_config(path)


def test_invalid_motor_calibration_does_not_claim_gpio(factory):
    from intsys_car_v1 import Motor
    for value in (float('nan'), True, -1, 100):
        with pytest.raises(ValueError):
            Motor(13, 23, factory, min_speed_to_move=value)
    motor = Motor(13, 23, factory, min_speed_to_move=25)
    motor.close()
