import logging
from time import sleep

from ..robot_hat.i2c import BUS_LOCK, I2C

logger = logging.getLogger("intsys_car_v1.AnalogGrayscale")

# ADC input An is read through register 0x17 - n (A0 = 0x17 = 23 ... A7 = 0x10 = 16).
ADC_REGISTERS = range(0x10, 0x18)


def adc_register(channel):
    """Register of an ADC input: "A0".."A7" or the register number itself (23 = A0)."""
    if isinstance(channel, str):
        if not (channel.upper().startswith("A") and channel[1:].isdigit() and int(channel[1:]) < 8):
            raise ValueError(f'ADC-Kanal muss "A0" bis "A7" sein, nicht "{channel}"')
        return 0x17 - int(channel[1:])
    if channel not in ADC_REGISTERS:
        # Kept working for cars configured like this, but the value is not a defined measurement.
        logger.warning("Kanal %s ist kein ADC-Eingang (A0-A7 = Register 23..16); Messwerte sind unzuverlässig",
                       channel)
    return channel


class AnalogGrayscale(I2C):
    # Same MCU as the PWM; its address depends on the Robot HAT version.
    ADDR = [0x14, 0x15, 0x16]

    def __init__(self, threshold=300, channel_left=0x17, channel_center=0x16, channel_right=0x15, address=None):
        super().__init__(self.ADDR if address is None else address)
        self.threshold = threshold

        self.greyscale_module_channels = {
            "left": adc_register(channel_left),
            "center": adc_register(channel_center),
            "right": adc_register(channel_right),
        }

    def get_line_detection_value(self, register):
        # One transaction under the bus lock: request the channel, then read MSB and LSB.
        # smbus only supports 8 bit register addresses, so write 2 bytes 0 with it.
        # A failed step repeats the whole transaction, never a single read.
        error = None
        for _ in range(self.RETRY):
            try:
                with BUS_LOCK:
                    self._smbus.write_word_data(self.address, register, 0)
                    msb = self._smbus.read_byte(self.address)
                    lsb = self._smbus.read_byte(self.address)
                return (msb << 8) | lsb
            except OSError as exc:
                error = exc
        raise error

    def get_line_detection_data(self):
        left = self.get_line_detection_value(self.greyscale_module_channels["left"])
        center = self.get_line_detection_value(self.greyscale_module_channels["center"])
        right = self.get_line_detection_value(self.greyscale_module_channels["right"])
        return dict(leftValue=left,
                    leftDetected=left < self.threshold,
                    centerValue=center,
                    centerDetected=center < self.threshold,
                    rightValue=right,
                    rightDetected=right < self.threshold,
                    )

def main():
    line_detection = AnalogGrayscale()
    loop(line_detection)

def loop(line_detection):
    while True:
        print(line_detection.get_line_detection_data())
        sleep(0.2)

if __name__ == '__main__':
    main()
