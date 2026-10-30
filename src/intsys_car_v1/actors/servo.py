#!/usr/bin/env python3
import numbers

from ..robot_hat import PWM

def mapping(x, in_min, in_max, out_min, out_max):
    """
    Map value from one range to another range

    :param x: value to map
    :type x: float/int
    :param in_min: input minimum
    :type in_min: float/int
    :param in_max: input maximum
    :type in_max: float/int
    :param out_min: output minimum
    :type out_min: float/int
    :param out_max: output maximum
    :type out_max: float/int
    :return: mapped value
    :rtype: float/int
    """
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

class Servo(PWM):
    """Servo motor class"""
    MAX_PW = 2500
    MIN_PW = 500
    FREQ = 50
    PERIOD = 4095
    _min = -90
    _max = 90
    _invert_angle = False
    _compensate_angle = 0

    @property
    def min_angle(self):
        return self._min

    @min_angle.setter
    def min_angle(self, value):
        # Security check, servo range is max -90 to 90
        self._min = max(value, -90)

    @property
    def max_angle(self):
        return self._max

    @max_angle.setter
    def max_angle(self, value):
        # Security check, servo range is max -90 to 90
        self._max = min(value, 90)

    @property
    def invert_angle(self):
        return self._invert_angle

    @invert_angle.setter
    def invert_angle(self, value):
        self._invert_angle = value

    @property
    def compensate_angle(self):
        return self._compensate_angle

    @compensate_angle.setter
    def compensate_angle(self, value):
        self._compensate_angle = value

    def __init__(self, name, channel, address=None, min_angle=-90, max_angle=90, compensate_angle=0, invert_angle=False, *args, **kwargs):
        """
        Initialize the servo motor class

        :param channel: PWM channel number(0-14/P0-P14)
        :type channel: int/str
        :param min_angle/max_angle: allowed angles relative to the calibrated centre
        :param compensate_angle: offset of the calibrated centre (servo horn position)
        :param invert_angle: servo is mounted mirrored
        """
        super().__init__(channel, address, *args, **kwargs)
        self._debug(f"Initializing servo motor {name} on {channel}")
        self.name = name
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.invert_angle = invert_angle
        self.period(self.PERIOD)
        self.compensate_angle = compensate_angle
        prescaler = self.CLOCK / self.FREQ / self.PERIOD
        self.prescaler(prescaler)

    def angle(self, angle):
        self.setAngle(angle)

    def setAngle(self, angle):
        """
        Set the angle of the servo motor

        :param angle: angle(-90~90)
        :type angle: float
        """
        if isinstance(angle, bool) or not isinstance(angle, numbers.Real):
            raise ValueError(
                "Angle value should be int or float value, not %s" % type(angle))
        # Limits apply to the requested angle, i.e. symmetric around the calibrated
        # centre; compensating first would cut one side (e.g. -10 -> left 20, right 30).
        angle = max(self.min_angle, min(self.max_angle, float(angle)))
        angle += self.compensate_angle
        if self.invert_angle:
            angle = -angle
        # Physical range of the servo.
        angle = max(-90.0, min(90.0, angle))
        self._debug(f"Set angle to: {angle}")
        pulse_width_time = mapping(angle, -90, 90, self.MIN_PW, self.MAX_PW)
        self._debug(f"Pulse width: {pulse_width_time}")
        self.pulse_width_time(pulse_width_time)

    def pulse_width_time(self, pulse_width_time):
        """
        Set the pulse width of the servo motor

        :param pulse_width_time: pulse width time(500~2500)
        :type pulse_width_time: float
        """
        if pulse_width_time > self.MAX_PW:
            pulse_width_time = self.MAX_PW
        if pulse_width_time < self.MIN_PW:
            pulse_width_time = self.MIN_PW

        pwr = pulse_width_time / 20000
        self._debug(f"pulse width rate: {pwr}")
        value = int(pwr * self.PERIOD)
        self._debug(f"pulse width value: {value}")
        self.pulse_width(value)

def main():
    cam_pan_servo = Servo("Kamera schwenken", "P0", invert_angle=True, compensate_angle=-6)
    cam_tilt_servo = Servo("Kamera neigen", "P1", min_angle=-65, max_angle=40)
    steering_servo = Servo("Lenkung", "P2", min_angle=-30, max_angle=30, compensate_angle=-10)
    cam_pan_servo.angle(0)
    cam_tilt_servo.angle(0)
    steering_servo.angle(0)

if __name__ == '__main__':
    main()
