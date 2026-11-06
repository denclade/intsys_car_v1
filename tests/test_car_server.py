"""Protocol, CarServer and Car without a Raspberry Pi: real ZeroMQ over localhost,
I2C is a fake bus, GPIO is gpiozero's MockFactory, the camera is a numpy array."""
import socket
import threading
import time

import numpy as np
import pytest
import zmq
from gpiozero.pins.mock import MockFactory
from test_library import FakeBus

from intsys_car_v1 import CAR_CONFIG_STANDARD, Car, CarServer
from intsys_car_v1.comm_lib import SerializingContext
from intsys_car_v1.protocol import check_calibration, parse_reply
from intsys_car_v1.robot_hat import i2c

IMAGE = np.zeros((48, 64, 3), np.uint8)
STOP = {"speed": 0.0, "left": 0.0, "right": 0.0, "steering": 0.0, "pan": 0.0, "tilt": 0.0}


@pytest.fixture(autouse=True)
def fake_bus(monkeypatch):
    monkeypatch.setattr(i2c, "SMBus", FakeBus)
    i2c._detected_addresses.clear()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --- protocol -----------------------------------------------------------------

def test_empty_reply_stops():
    assert parse_reply({}) == STOP
    assert str(parse_reply({"controller": {"rt": 0, "lt": 0}})["speed"]) == "0.0"


def test_controller_data_like_the_standard():
    values = parse_reply({"controller": {"rt": 50, "lt": 0, "left_stick": {"left": 100},
                                         "right_stick": {"up": 50, "right": 50}}})
    assert values == {"speed": 50, "left": 50, "right": 50, "steering": -30, "pan": 45, "tilt": -32.5}
    assert parse_reply({"controller": {"rt": 0, "lt": 40}})["speed"] == -40


def test_direct_values_take_precedence_and_are_clamped():
    values = parse_reply({"controller": {"rt": 80, "steering": 10}, "speed": 20, "steering": 99, "motor": 5})
    assert values["speed"] == 20 and values["steering"] == 30
    assert parse_reply({"drive": {"speed": 30, "angle": -12}})["steering"] == -12
    assert parse_reply({"throttle": 15, "left": -200})["left"] == -100


@pytest.mark.parametrize("reply", [None, {"speed": "fast"}, {"speed": float("nan")}, {"controller": {"rt": []}}])
def test_invalid_replies_raise(reply):
    with pytest.raises(ValueError):
        parse_reply(reply)


def test_calibration_all_or_nothing():
    assert check_calibration({"servo_steering": {"compensate_angle": -8}}) == [
        ("servo_steering", "compensate_angle", -8.0)]
    for bad in ({"servo_steering": {"compensate_angle": 46}}, {"motor_left": {"pwm_pin": 1}},
                {"servo_pan": {"compensate_angle": True}}, {"servo_pan": 3}, []):
        with pytest.raises(ValueError):
            check_calibration(bad)


# --- CarServer ----------------------------------------------------------------

class FakeVehicle:
    """The vehicle side of the protocol, as SPARK-E or record_drive.py speak it."""

    def __init__(self, port):
        self.context = SerializingContext()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.setsockopt(zmq.RCVTIMEO, 2000)
        self.socket.connect(f"tcp://127.0.0.1:{port}")

    def exchange(self, message):
        self.socket.send_img(IMAGE, message)
        return self.socket.recv_json()

    def close(self):
        self.socket.close(linger=0)
        self.context.term()


@pytest.fixture
def server():
    port = free_port()
    car = CarServer(port=port, show_image=False)
    vehicle = FakeVehicle(port)
    yield car, vehicle
    car.close()
    vehicle.close()


def run_frames(car, vehicle, messages, body):
    """Vehicle sends each message; the server loop runs body once per frame."""
    replies = []
    thread = threading.Thread(target=lambda: replies.extend(vehicle.exchange(m) for m in messages))
    thread.start()
    for _ in messages:
        assert car.next_frame()
        body(car)
    car.close()  # sends the reply to the last frame
    thread.join(5)
    return replies


def test_server_values_stay_until_changed(server):
    car, vehicle = server
    sensors = {"ultraschall": 12.5, "linien": {"leftDetected": True, "centerValue": 210}}
    seen = []

    def body(car):
        seen.append((car.frame_count, car.distance, car.line_left, car.line_right, car.line_center_value))
        if car.frame_count == 1:
            car.speed = 40
            car.steering = 99
        if car.frame_count == 2:
            car.left = 10

    replies = run_frames(car, vehicle, [sensors, {"ultraschall": None}, {}], body)
    assert seen == [(1, 12.5, True, False, 210), (2, 999.0, False, False, None), (3, 999.0, False, False, None)]
    assert replies[0] == {"speed": 40.0, "steering": 30.0}
    assert replies[1] == {"speed": 40.0, "steering": 30.0, "left": 10.0}
    assert replies[2] == {}  # close() answers the last frame with "stop"
    assert car.right == 40.0


def test_setting_speed_drives_both_motors_again(server):
    car, _ = server
    car.left, car.right = 10, -10
    car.speed = 20
    assert (car.left, car.right) == (20.0, 20.0)
    with pytest.raises(ValueError):
        car.speed = "schnell"


def test_server_calibration_is_sent_once(server):
    car, vehicle = server

    def body(car):
        if car.frame_count == 1:
            car.calibrate("servo_steering", "compensate_angle", -8)

    replies = run_frames(car, vehicle, [{}, {}], body)
    assert replies == [{"calibration": {"servo_steering": {"compensate_angle": -8}}}, {}]
    with pytest.raises(ValueError):
        car.calibrate("servo_steering", "compensate_angle", 60)


def test_drive_manually_keys_override_controller(server, monkeypatch):
    car, _ = server
    pressed = set()
    monkeypatch.setattr(CarServer, "key_pressed", lambda self, key: key in pressed)
    monkeypatch.setattr(CarServer, "controller", property(
        lambda self: {"rt": 50, "left_stick": {"right": 50}, "right_stick": {"left": 100}}))
    car.drive_manually()
    assert (car.speed, car.steering, car.pan) == (50, 15, -90)
    pressed.update({"s", "a"})
    car.drive_manually()
    assert (car.speed, car.steering, car.pan) == (-100, -30, -90)


def test_next_frame_after_close_ends_the_loop(server):
    car, _ = server
    car.close()
    assert not car.next_frame()


# --- Car ----------------------------------------------------------------------

@pytest.fixture
def car(monkeypatch):
    factory = MockFactory()
    vehicle = Car(config=CAR_CONFIG_STANDARD, factory=factory)
    speeds = {"left": [], "right": []}
    for side in speeds:
        motor = getattr(vehicle, f"motor_{side}")
        monkeypatch.setattr(motor, "setSpeed", lambda value, side=side: speeds[side].append(value))
    angles = {name: [] for name in ("steering", "pan", "tilt")}
    for name in angles:
        servo = getattr(vehicle, f"servo_{name}")
        monkeypatch.setattr(servo, "setAngle", lambda value, name=name: angles[name].append(value))
    monkeypatch.setattr(vehicle.ultrasonic, "get_distance_cm", lambda: None)
    monkeypatch.setattr(vehicle.line_sensor, "get_line_detection_data",
                        lambda: {"leftDetected": False, "centerDetected": True, "rightDetected": False})
    monkeypatch.setattr(vehicle, "_capture", lambda: IMAGE)
    vehicle.speeds, vehicle.angles = speeds, angles
    yield vehicle
    vehicle.close()
    factory.close()


def test_car_properties_drive_the_hardware(car):
    car.speed = 40
    car.steering = -50
    car.left = -20
    assert car.speeds == {"left": [40.0, -20.0], "right": [40.0]}
    assert car.angles["steering"] == [-30.0]
    assert (car.speed, car.left, car.right, car.steering) == (40.0, -20.0, 40.0, -30.0)
    assert car.distance == 999.0 and car.line_center and not car.line_left


def test_car_without_server_just_loops(car):
    assert car.next_frame() and car.next_frame()
    assert car.frame_count == 2


def test_car_and_server_end_to_end(car):
    port = free_port()
    server = CarServer(port=port, show_image=False)
    car.connect("127.0.0.1", port)
    seen = []

    def server_loop():
        while server.next_frame():
            seen.append((server.distance, server.line_center, server.message["actor_data"]["speed"]))
            if server.frame_count == 1:
                server.speed = 40
                server.steering = 20
                server.calibrate("motor_left", "min_speed_to_move", 30)
            if server.frame_count == 3:
                server.close()  # the reply to frame 3 is empty: stop

    thread = threading.Thread(target=server_loop)
    thread.start()
    for _ in range(2):
        assert car.next_frame()
    assert (car.speed, car.left, car.steering) == (40.0, 40.0, 20.0)
    assert car.motor_left.min_speed_to_move == 30
    assert car.sensor_data()["calibration"]["motor_left"]["min_speed_to_move"] == 30
    assert car.next_frame()
    thread.join(5)
    assert seen == [(999.0, True, 0.0), (999.0, True, 40.0), (999.0, True, 40.0)]
    assert (car.speed, car.left, car.right, car.steering) == (0.0, 0.0, 0.0, 0.0)


def test_car_reports_rejected_calibration_once(car):
    car._apply_calibration({"servo_tilt": {"compensate_angle": 99}})
    assert "servo_tilt" in car.sensor_data()["calibration_error"]
    assert car.servo_tilt.compensate_angle == 0


# --- vision -------------------------------------------------------------------

def test_brightness_and_color_share(server, tmp_path):
    car, _ = server
    assert car.brightness() == 0.0 and car.color_share("red") == 0.0  # no image yet
    image = np.zeros((30, 90, 3), np.uint8)
    image[:, :30] = (0, 0, 255)       # left third red (BGR)
    image[:, 60:] = (255, 255, 255)   # right third white
    car.image = image
    assert car.brightness("right") == 255.0 and car.brightness("center") == 0.0
    assert 0 < car.brightness("left") < 255
    assert car.color_share("red") == 33.3 and car.color_share("red", "left") == 100.0
    assert car.color_share("blue") == 0.0
    with pytest.raises(ValueError):
        car.color_share("lila")
    first, second = car.save_image(tmp_path / "bilder"), car.save_image(tmp_path / "bilder")
    assert first.endswith("bild_1.jpg") and second.endswith("bild_2.jpg")


def test_car_image_analysis(car):
    assert car.brightness("all") == 0.0 and car.color_share("green") == 0.0


# --- Xbox controller ------------------------------------------------------------

class FakePygame:
    """Just enough of pygame.joystick for XboxController."""

    class error(Exception):
        pass

    def __init__(self):
        self.axes = None  # None = no controller plugged in
        fake = self

        class Joystick:
            def __init__(self, index):
                pass

            def init(self):
                pass

            def get_name(self):
                return "Xbox 360 Controller"

            def get_axis(self, index):
                if fake.axes is None:
                    raise fake.error("unplugged")
                return fake.axes[index]

        class joystick:
            init = staticmethod(lambda: None)
            get_count = staticmethod(lambda: 0 if fake.axes is None else 1)

        joystick.Joystick = Joystick
        self.joystick = joystick


def test_xbox_controller_hotplug_and_mapping():
    from intsys_car_v1.controller import XboxController, neutral
    pygame = FakePygame()
    xbox = XboxController(pygame)
    assert xbox.read() == neutral() and not xbox.connected
    pygame.axes = [-1.0, 0.05, -1.0, 0.5, -0.5, 1.0]  # stick left, nearly centred, rt pressed
    data = xbox.read()
    assert xbox.connected and xbox.name == "Xbox 360 Controller"
    assert data["left_stick"] == {"left": 100, "right": 0, "up": 0, "down": 0}
    assert data["right_stick"] == {"left": 0, "right": 50, "up": 50, "down": 0}
    assert (data["lt"], data["rt"]) == (0, 100)
    assert parse_reply({"controller": data})["steering"] == -30
    pygame.axes = None
    assert xbox.read() == neutral() and not xbox.connected


def test_car_calibrate_directly_and_stop(car):
    car.steering = 10
    car.calibrate("servo_steering", "compensate_angle", -8)
    assert car.servo_steering.compensate_angle == -8
    assert car.angles["steering"][-1] == 10.0  # servo moves to the new centre
    with pytest.raises(ValueError):
        car.calibrate("servo_steering", "compensate_angle", 99)
    car.speed = 40
    car.stop()
    assert (car.speed, car.left, car.steering, car.pan) == (0.0, 0.0, 0.0, 0.0)


def test_server_stop(server):
    car, _ = server
    car.left, car.steering = 30, 20
    car.stop()
    assert (car.speed, car.left, car.right, car.steering) == (0.0, 0.0, 0.0, 0.0)


def stop_ultrasonic(car):
    car._ultrasonic_stop.set()
    car._ultrasonic_thread.join()


def test_car_frames_do_not_wait_for_ultrasonic(car, monkeypatch):
    entered, finish = threading.Event(), threading.Event()

    def slow_distance():
        entered.set()
        finish.wait(1)
        return 42

    monkeypatch.setattr(car.ultrasonic, "get_distance_cm", slow_distance)
    try:
        assert entered.wait(1)
        started = time.monotonic()
        assert car.next_frame() and car.next_frame()
        assert time.monotonic() - started < 0.5
        assert car.sensor_data()["ultraschall"] is None
        assert not finish.is_set()
        finish.set()
        stop_ultrasonic(car)
        assert car._measure(force=True)["ultraschall"] == 42
    finally:
        finish.set()


def test_car_waits_for_first_distance(car):
    assert car._distance_sample is not None  # Car() hat auf die erste Messung gewartet
    assert "ultrasonic" not in car.sensor_errors


def test_car_stale_distance_is_unknown(car):
    stop_ultrasonic(car)
    car._distance_sample = (12.0, None, time.monotonic())
    assert car._measure(force=True)["ultraschall"] == 12.0 and car.distance == 12.0
    car._distance_sample = (12.0, None, time.monotonic() - 1)
    assert car._measure(force=True)["ultraschall"] is None and car.distance == 999.0
    assert "veraltet" in car.sensor_errors["ultrasonic"]


def test_car_ultrasonic_thread_survives_unexpected_errors(car, monkeypatch):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("kaputt")
        return 33.0

    monkeypatch.setattr(car.ultrasonic, "get_distance_cm", flaky)
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.01)
    assert car._ultrasonic_thread.is_alive()
    assert car._measure(force=True)["ultraschall"] == 33.0


def test_car_frame_rate_keeps_a_fixed_grid(car):
    started = time.monotonic()
    for _ in range(31):
        car.next_frame()
    assert 0.95 < time.monotonic() - started < 1.15  # 30 Abstände zu 1/30 s


def test_car_sends_timing(car):
    data = car.sensor_data()
    timing = data["timing"]
    assert abs(timing["sent"] - time.time()) < 1
    assert timing["image_age"] is None  # Testbild ohne Kamera-Metadaten
    assert 0 <= timing["ultrasonic_age"] < 0.5


def test_exposure_time_from_libcamera_timestamp():
    from intsys_car_v1.car import _exposure_time
    assert _exposure_time({}) is None and _exposure_time({"SensorTimestamp": 0}) is None
    stamp = time.clock_gettime_ns(time.CLOCK_BOOTTIME) - 30_000_000
    assert 0.029 <= time.monotonic() - _exposure_time({"SensorTimestamp": stamp}) < 0.5
