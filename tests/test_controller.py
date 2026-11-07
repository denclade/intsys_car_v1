"""Backend-independent mapping, device lifecycle and fallback without real hardware."""
from types import SimpleNamespace

import pytest

from intsys_car_v1 import controller, _sdl3_controller


@pytest.fixture(autouse=True)
def no_system_backend(monkeypatch):
    monkeypatch.delenv('INTSYS_CONTROLLER', raising=False)
    monkeypatch.setattr(controller, 'find_sdl3_library', lambda: None)


@pytest.mark.parametrize('name', ['Xbox 360 Controller', 'Steam Controller', 'Steam Virtual Gamepad'])
def test_mapped_controllers_have_identical_controls_and_hotplug(name):
    device = SimpleNamespace(name=name, attached=lambda: attached[0],
                             get_axis=lambda index: axes[index], quit=lambda: closed.append(True))
    attached, axes, closed = [True], [-32768, 0, 16384, -32768, 0, 32767], []
    api = SimpleNamespace(init=lambda: None, is_controller=lambda index: True,
                          Controller=lambda index: device)
    pg = SimpleNamespace(error=RuntimeError, _sdl2=SimpleNamespace(controller=api),
                         joystick=SimpleNamespace(init=lambda: None, get_count=lambda: int(attached[0])))
    pad = controller.Gamepad(pg, backend='pygame')
    values = pad.read()
    assert pad.connected and pad.name == name
    assert values['left_stick']['left'] == 100
    assert values['right_stick'] == {'left': 0, 'right': 50, 'up': 100, 'down': 0}
    assert (values['lt'], values['rt']) == (0, 100)
    attached[0] = False
    assert pad.read() == controller.neutral()
    assert pad.name is None and not pad.connected and closed == [True]
    attached[0] = True
    assert pad.read() == values
    pad.close()
    pad.close()
    assert closed == [True, True] and not pad.available


def test_sdl3_hotplug_mapping_and_close(monkeypatch):
    attached, closed, hints = [True], [], []
    sdl = SimpleNamespace(
        version=(3, 4, 0), SetHint=lambda *args: hints.append(args),
        InitSubSystem=lambda flags: True, SetJoystickEventsEnabled=lambda enabled: None,
        SetGamepadEventsEnabled=lambda enabled: None, UpdateGamepads=lambda: None,
        PumpEvents=lambda: None,
        gamepad_ids=lambda: [42] if attached[0] else [], OpenGamepad=lambda index: index,
        GetGamepadName=lambda device: b'Steam Controller', GamepadConnected=lambda device: attached[0],
        GetGamepadAxis=lambda device, index: 32767 if index == 5 else 0,
        CloseGamepad=lambda device: closed.append(device), QuitSubSystem=lambda flags: closed.append('quit'))
    monkeypatch.setattr(_sdl3_controller, '_Sdl3', lambda library: sdl)
    monkeypatch.setattr(controller, 'find_sdl3_library', lambda: 'fake-sdl3')
    pad = controller.Gamepad()
    assert pad.backend == 'sdl3' and pad.read()['rt'] == 100 and pad.kind == 'steam'
    attached[0] = False
    assert pad.read() == controller.neutral() and pad.name is None
    attached[0] = True
    assert pad.read()['rt'] == 100
    pad.close()
    pad.close()
    assert closed == [42, 42, 'quit']
    assert (b'SDL_JOYSTICK_HIDAPI_STEAM', b'1') in hints


def test_unavailable_backend_is_neutral(monkeypatch):
    monkeypatch.setattr(controller.Gamepad, '_import_pygame', lambda self: None)
    pad = controller.Gamepad()
    assert not pad.available and pad.read() == controller.neutral()
    assert pad.name is None and pad.kind is None
    pad.close()


def test_invalid_backend_is_rejected():
    with pytest.raises(ValueError):
        controller.Gamepad(backend='invalid')


def test_broken_sdl3_falls_back_to_pygame(monkeypatch):
    from test_car_server import FakePygame
    monkeypatch.setattr(controller, 'find_sdl3_library', lambda: '/missing/libSDL3.so')
    pad = controller.Gamepad(FakePygame())
    assert pad.backend == 'pygame' and pad.sdl3_error
    assert pad.read() == controller.neutral()
    pad.close()


def test_unknown_raw_layout_is_not_treated_as_xbox():
    closed = []
    device = SimpleNamespace(init=lambda: None, quit=lambda: closed.append(True),
                             get_name=lambda: 'Unmapped Steam Controller')
    pg = SimpleNamespace(error=RuntimeError, joystick=SimpleNamespace(
        init=lambda: None, get_count=lambda: 1, Joystick=lambda index: device))
    pad = controller.Gamepad(pg, backend='pygame')
    assert pad.read() == controller.neutral() and not pad.connected
    assert closed == [True]
    pad.close()


def test_initialization_requires_main_thread():
    import threading
    errors = []

    def initialize():
        try:
            controller.Gamepad()
        except RuntimeError as error:
            errors.append(str(error))

    thread = threading.Thread(target=initialize)
    thread.start()
    thread.join()
    assert len(errors) == 1 and 'Hauptthread' in errors[0]


def test_failed_pygame_initialization_returns_neutral():
    def fail():
        raise RuntimeError('joystick unavailable')

    pg = SimpleNamespace(error=RuntimeError, joystick=SimpleNamespace(init=fail))
    pad = controller.Gamepad(pg, backend='pygame', quiet=True)
    assert not pad.available and pad.read() == controller.neutral()
    pad.close()


def test_cli_fails_if_requested_backend_is_unavailable(monkeypatch):
    from intsys_car_v1 import controller_cli
    monkeypatch.setattr(controller.Gamepad, '_import_pygame', lambda self: None)
    assert controller_cli.main(['--backend', 'sdl3', '--once']) == 1
