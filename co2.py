"""
CO2 sensor abstraction
"""

import logging

import adafruit_scd4x


class CO2SensorException(Exception):
    """
    CO2 sensor error
    """


# pylint: disable=too-few-public-methods
class CO2Sensor:
    """
    CO2 sensor abstraction
    """

    def __init__(self, i2c):
        """
        initialize CO2 sensor
        """
        self.logger = logging.getLogger(__name__)

        self.scd4x_sensor = None
        try:
            self.scd4x_sensor = adafruit_scd4x.SCD4X(i2c)
            self.logger.info("SCD4x sensor connected")
        except ValueError as exception:
            raise CO2SensorException(
                f"cannot find SCD4x sensor: {exception}"
            ) from exception

        self.logger.info("Waiting for the first measurement from the SCD-40 sensor")
        self.scd4x_sensor.start_periodic_measurement()

    def get_data(self):
        """
        Reads CO2 and relative humidity from the SCD4x sensor.
        :return: CO2 PPM, relative humidity values
        """
        co2_ppm = None
        humidity = None
        if self.scd4x_sensor.data_ready:
            co2_ppm = self.scd4x_sensor.CO2
            self.logger.debug(f"CO2 ppm={co2_ppm}")
            humidity = self.scd4x_sensor.relative_humidity
            self.logger.debug(f"humidity={humidity:.1f}%")

        return co2_ppm, humidity
