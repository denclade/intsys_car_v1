import time

import gpiozero

from ..robot_hat import PWM

# Motor Left PWM P13
# Motor Left Dir Pin: D4 - 23
# Motor Right PWM P12
# Motor Right Dir Pin: D5 - 24

class Motor:
    """Motor"""
    PERIOD = 4095
    PRESCALER = 10
    DEFAULT_FREQ = 100  # Hz
    _is_reversed = False
    _is_in_reverse = False
    _speed = 0
    _min_speed_to_move = 0

    def __init__(self, pwm_pin, dir_pin, factory, min_speed_to_move=0, is_reversed=False, freq=DEFAULT_FREQ):
        """
        Initialize a motor

        :param pwm_pin: Motor speed control pwm pin (e.g. 13 or "P13")
        :param dir_pin: Motor direction control GPIO pin
        :param factory: gpiozero pin factory, e.g. LGPIOFactory()
        :param min_speed_to_move: PWM percentage the motor needs to start turning;
            speeds 1..100 are mapped to min_speed_to_move..100 in both directions
        :param is_reversed: motor is mounted the other way round
        """

        self.dir = gpiozero.OutputDevice(dir_pin, pin_factory=factory)
        self.freq = freq
        self._is_reversed = is_reversed
        self._min_speed_to_move = min_speed_to_move
        self.pwm = PWM(pwm_pin)
        self.pwm.freq(self.freq)
        self.pwm.pulse_width_percent(0)

        self._speed = 0

    @property
    def is_reversed(self):
        return self._is_in_reverse

    @is_reversed.setter
    def is_reversed(self, value):
        if self._is_reversed:
            value = not value
        if value:
            self.dir.on()
        else:
            self.dir.off()
        self._is_in_reverse = value

    @property
    def min_speed_to_move(self):
        """PWM percentage the motor needs to start turning (calibration, 0..100)."""
        return self._min_speed_to_move

    @min_speed_to_move.setter
    def min_speed_to_move(self, value):
        if not 0 <= value < 100:
            raise ValueError(f"min_speed_to_move must be between 0 and 100, not {value}")
        self._min_speed_to_move = value
        # Apply to a running motor right away (live calibration).
        if self._speed:
            self.speed = self._speed

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, value):
        # Ensure speed to be between -100 and 100
        value = max(min(100, value), -100)

        self._speed = value
        if value == 0:
            self.pwm.pulse_width_percent(0)
            return
        scaled_speed = self._min_speed_to_move + abs(value) / 100 * (100 - self._min_speed_to_move)
        # Switch direction first, so the motor never gets the new power in the old direction.
        self.is_reversed = value < 0
        self.pwm.pulse_width_percent(scaled_speed)

    def setSpeed(self, speed):
        self.speed = speed

    def close(self):
        """Stop the motor and release the direction pin."""
        try:
            self.pwm.pulse_width_percent(0)
        finally:
            self.dir.close()


def main():
    from gpiozero.pins.lgpio import LGPIOFactory
    factory = LGPIOFactory()
    motor_left = Motor("P13", 23, factory, min_speed_to_move=25)
    motor_left.setSpeed(0)
    motor_right = Motor("P12", 24, factory, is_reversed=True, min_speed_to_move=25)
    motor_right.speed = 0
    while True:
        # Program needs to be running, if not the dir_pin is reset.
        time.sleep(1)



if __name__ == '__main__':
    main()
