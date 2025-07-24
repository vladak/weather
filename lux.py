"""
light sensor abstraction
"""

import logging

import adafruit_veml7700


class LuxSensorException(Exception):
    """
    LuxSensor errors
    """


# pylint: disable=too-few-public-methods
class LuxSensor:
    """
    light sensor abstraction
    """

    def __init__(self, i2c):
        """
        initialize the sensor
        """
        try:
            self.veml7700_sensor = adafruit_veml7700.VEML7700(i2c)
            logger.info("VEML7700 sensor connected")
        except RuntimeError as exception:
            raise LuxSensorException(
                f"cannot instantiate VEML7700 sensor: {exception}"
            ) from exception

    def _acquire_light(self):
        """
        Reads light amount in the form of Lux
        :param light_sensor light sensor object
        :return: lux value or None on error
        """

        logger = logging.getLogger(__name__)

        lux = self.veml7700_sensor.light
        logger.debug(f"lux={lux}")
        return lux

    def get_value(self):
        """
        get value in Lux
        """
        return self._acquire_light()
