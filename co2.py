"""
CO2 sensor abstraction
"""

import logging

import adafruit_scd4x


class CO2SensorException(Exception):
    """
    CO2 sensor error
    """


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

    def get_co2ppm(self):
        """
        Reads CO2 from the SCD4x sensor.
        :return: CO2 PPM value
        """
        co2_ppm = self.scd4x_sensor.CO2
        if co2_ppm:
            self.logger.debug(f"CO2 ppm={co2_ppm}")
        return co2_ppm

    def get_humidity(self):
        """
        Reads humidity from the SCD4x sensor.
        :return: relative humidity value
        """
        humidity = self.scd4x_sensor.relative_humidity
        if humidity:
            self.logger.debug(f"humidity={humidity:.1f}%")
        return humidity
