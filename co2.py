"""
CO2 sensor abstraction
"""

import logging

try:
    from typing import Tuple
except ImportError:
    pass

try:
    import adafruit_scd4x
except ImportError:
    pass
try:
    import adafruit_stcc4
except ImportError:
    pass


class CO2SensorException(Exception):
    """
    CO2 sensor error
    """


# pylint: disable=too-few-public-methods
class CO2Sensor:
    """
    CO2 sensor abstraction (auto-detect SCD4x → fallback to STCC4)
    """

    def __init__(self, i2c):
        self.logger = logging.getLogger(__name__)
        self.sensor = None
        self.sensor_name = None

        # Try SCD4x first.
        try:
            self.sensor = adafruit_scd4x.SCD4X(i2c)
            self.sensor.start_periodic_measurement()
            self.sensor_name = "SCD4x"
            self.logger.info("SCD4x sensor connected")
            return
        except (ValueError, RuntimeError) as scd_error:
            self.logger.info(f"cannot find SCD4x sensor: {scd_error}")
        except NameError:
            self.logger.warning("No library for the SCD4x sensor")

        # Fallback to STCC4.
        self.logger.info("SCD4x not available, trying STCC4")
        try:
            self.sensor = adafruit_stcc4.STCC4(i2c)
            self.sensor.continuous_measurement = True
            self.sensor_name = "STCC4"
            self.logger.info("STCC4 sensor connected")
            return
        except (RuntimeError, ValueError) as stcc_error:
            self.logger.info(f"cannot find STCC4 sensor: {stcc_error}")
        except NameError:
            self.logger.warning("No library for the STCC4 sensor")

        raise CO2SensorException("no supported CO2 sensor found")

    def get_data(self) -> Tuple[
        int | type[None],
        float | type[None],
    ]:
        """
        Reads CO2 and relative humidity.
        :return: (co2_ppm, humidity_pct) or (None, None) if not ready (SCD4x only)
        or if no sensor found
        """

        if self.sensor is None:
            return None, None

        # SCD4x requires data_ready check.
        if self.sensor_name == "SCD4x":
            if not self.sensor.data_ready:
                return None, None

        co2_ppm = self.sensor.CO2
        humidity = self.sensor.relative_humidity

        return co2_ppm, humidity
