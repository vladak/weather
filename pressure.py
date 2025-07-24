"""
pressure sensor abstraction
"""

import logging

import adafruit_bmp280


def sea_level_pressure(pressure, outside_temp, altitude):
    """
    Convert sensor pressure value to value at the sea level.
    The formula uses outside temperature to compensate.
    :param pressure: measured pressure
    :param outside_temp: outside temperature in degrees of Celsius (float)
    :param altitude: altitude
    :return: pressure at sea level
    """
    temp_comp = outside_temp + 273.15
    return pressure / pow(1.0 - 0.0065 * int(altitude) / temp_comp, 5.255)


class PressureSensorException(Exception):
    """
    pressure sensor errors
    """


# pylint: disable=too-few-public-methods
class PressureSensor:
    """
    pressure sensor abstraction
    """

    def __init__(self, i2c):
        """
        initalize pressure sensor
        """
        self.logger = logging.getLogger(__name__)

        try:
            self.bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)
            self.logger.info("BMP280 sensor connected")
        except RuntimeError as exception:
            raise PressureSensorException(
                f"cannot instantiate BMP280 sensor: {exception}"
            ) from exception

    def _acquire_pressure(self, altitude, outside_temp):
        """
        Read data from the pressure sensor and calculate pressure at sea level.
        :param bmp_sensor:
        :param altitude: altitude in meters
        :param outside_temp: outside temperature in degrees of Celsius
        :return: pressure value
        """

        pressure_val = self.bmp_sensor.pressure
        if pressure_val and pressure_val > 0:
            self.logger.debug(f"pressure={pressure_val}")
            if outside_temp:
                pressure_val = sea_level_pressure(pressure_val, outside_temp, altitude)
                self.logger.debug(f"pressure at sea level={pressure_val}")

        return pressure_val

    def get_value(self, altitude, outside_temp):
        """
        return pressure value
        """
        return self._acquire_pressure(altitude, outside_temp)
