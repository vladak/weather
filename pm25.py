"""
particle sensor abstraction
"""

import logging

from adafruit_pm25.i2c import PM25_I2C


# pylint: disable=too-few-public-methods
class PM25Sensor:
    """
    particle sensor abstraction
    """

    def __init__(self, i2c):
        """
        initialize the particle sensor
        """
        self.pm25_sensor = PM25_I2C(i2c, None)

    def _acquire_pm25(self):
        """
        Read PM25 data
        :param pm25_sensor: PM25 sensor object
        :return: data items or None
        """

        logger = logging.getLogger(__name__)

        try:
            acquired_data = self.pm25_sensor.read()
        except RuntimeError:
            logger.warning("Unable to read from PM25 sensor")
            return None

        logger.debug(f"PM25 data={acquired_data}")

        return acquired_data.items()

    def get_values(self):
        """
        return particle measurements
        """
        return self._acquire_pm25()
