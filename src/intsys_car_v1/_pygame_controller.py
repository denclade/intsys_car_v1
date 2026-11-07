"""pygame/SDL-2-Adapter mit GameController-Mapping und altem Xbox-360-Fallback."""
import importlib
import sys

from ._controller_mapping import AXES, GAMEPAD_AXES, _from_gamepad_api

def _pygame_controller_module(pygame):
    """pygame._sdl2.controller (GameController-API) oder None."""
    module = getattr(getattr(pygame, "_sdl2", None), "controller", None)
    if module is None and getattr(pygame, "__name__", "") in ("pygame",):
        try:
            module = importlib.import_module("pygame._sdl2.controller")
        except ImportError:
            module = None
    return module


class _PygameBackend:
    name = "pygame"

    def __init__(self, pygame):
        self._pygame = pygame
        self._controllers = _pygame_controller_module(pygame)
        pygame.joystick.init()
        if self._controllers is not None:
            try:
                self._controllers.init()
            except pygame.error:  # z. B. ohne Joystick-Subsystem: dann nur roh
                self._controllers = None
        self._device = None
        self._mapped = False  # True: GameController-API, False: rohe Achsen (AXES)
        self.device_name = None

    def poll(self):
        if self._device is not None and not self._attached():
            self.close()
        if self._device is None:
            try:
                self._open_first()
            except self._pygame.error:  # während der Erkennung abgesteckt
                self.close()
        return self._device is not None

    def _attached(self):
        try:
            if self._mapped:
                return bool(self._device.attached())
            if hasattr(self._device, "get_attached"):
                return self._device.get_attached()
            if hasattr(self._device, "get_instance_id"):
                instance_id = self._device.get_instance_id()
                return any(self._pygame.joystick.Joystick(i).get_instance_id() == instance_id
                           for i in range(self._pygame.joystick.get_count()))
            return self._pygame.joystick.get_count() > 0
        except self._pygame.error:
            return False

    def _open_first(self):
        count = self._pygame.joystick.get_count()
        controllers = self._controllers
        for index in range(count):
            if controllers is not None and controllers.is_controller(index):
                device = controllers.Controller(index)
                self._device, self._mapped = device, True
                self.device_name = getattr(device, "name", None) or "Gamepad"
                break
        else:
            if count > 0:
                device = self._pygame.joystick.Joystick(0)
                device.init()
                # Das alte rohe Layout gilt ausschließlich für Xbox 360 unter Linux.
                # Andere unbekannte Geräte brauchen ein SDL-Mapping statt geratener Achsen.
                name = device.get_name()
                if not (sys.platform.startswith("linux") and "360" in name.lower()
                        and ("xbox" in name.lower() or "x-box" in name.lower())):
                    device.quit()
                    return
                self._device, self._mapped = device, False
                self.device_name = name

    def axes(self):
        device = self._device
        try:
            if self._mapped:
                raw = {name: device.get_axis(index) for name, index in GAMEPAD_AXES.items()}
                return _from_gamepad_api(raw)
            axis = {name: device.get_axis(index) for name, index in AXES.items()}
        except self._pygame.error:  # gerade abgesteckt
            self.close()
            return None
        axis["lt"] = (axis["lt"] + 1) / 2  # rohe Trigger: −1 losgelassen … 1 gedrückt
        axis["rt"] = (axis["rt"] + 1) / 2
        return axis

    def close(self):
        if self._device is not None and hasattr(self._device, "quit"):
            try:
                self._device.quit()
            except self._pygame.error:
                pass
        self._device = None
        self.device_name = None
