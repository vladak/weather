"""
TVOC sensor abstraction
"""

import logging
import os
import time

import adafruit_ens160
import adafruit_sgp30

try:
    from adafruit_sgp41.sgp41 import SGP41
except ImportError:
    pass


class TVOCException(Exception):
    """
    TVOC exception
    """


# pylint: disable=too-few-public-methods
class TVOCSensor:
    """
    TVOC sensor abstraction
    """

    BASELINE_FILE = "tvoc_baselines.dat"

    def __init__(self, i2c):
        """
        Try to initialize on of the supported sensors.
        """
        self.logger = logging.getLogger()
        self.ens160_sensor = None
        self.sgp41_sensor = None
        self.sgp41_conditioning_start = None
        self.sgp30_sensor = None

        try:
            self.ens160_sensor = adafruit_ens160.ENS160(i2c)
            self.logger.info(
                f"ENS160 sensor present (firmware {self.ens160_sensor.firmware_version})"
            )
            return
        except (ValueError, RuntimeError) as exception:
            self.logger.error(f"cannot instantiate ENS160 sensor: {exception}")
            self.ens160_sensor = None

        # Try to fall back to SGP41 if ENS160 is not present or cannot be instantiated.
        if self.ens160_sensor is None:
            try:
                self.sgp41_sensor = SGP41(i2c)
                self.sgp41_conditioning_start = time.time()
                self.logger.info(
                    f"SGP41 sensor present (serial {self.sgp41_sensor.serial_number})"
                )
                return
            except (OSError, RuntimeError) as exception:
                self.logger.error(f"cannot instantiate SGP41 sensor: {exception}")
                self.sgp41_sensor = None
            except NameError:
                self.logger.warning("No library for the SGP41 sensor")

        # Try to fall back to SGP30 if ENS160/SGP41 are not present or cannot be instantiated.
        if self.ens160_sensor is None and self.sgp41_sensor is None:
            try:
                self.sgp30_sensor = adafruit_sgp30.Adafruit_SGP30(i2c)
                try:
                    tvoc_baseline, co2_baseline = self._read_baselines(
                        self.BASELINE_FILE
                    )
                    self.sgp30_sensor.set_iaq_baseline(co2_baseline, tvoc_baseline)
                except OSError as exception:
                    self.logger.error(
                        f"failed to get baselines for the SGP30 sensor: {exception}"
                    )
                return
            except (OSError, RuntimeError) as exception:
                self.logger.error(f"cannot instantiate SGP30 sensor: {exception}")
                self.sgp30_sensor = None

        raise TVOCException("no TVOC sensor available")

    def _write_baselines(self, file):
        """
        :param sgp30_sensor: sensor instance
        :param file: output file
        """
        tvoc_baseline = self.sgp30_sensor.baseline_TVOC
        co2_baseline = self.sgp30_sensor.baseline_eCO2

        if tvoc_baseline != 0 and co2_baseline != 0:
            self.logger.debug(
                f"writing baselines to {file}: TVOC={tvoc_baseline}, CO2={co2_baseline}"
            )

            with open(file, "wb") as file_obj:
                file_obj.write(tvoc_baseline.to_bytes(2, byteorder="big", signed=False))
                file_obj.write(co2_baseline.to_bytes(2, byteorder="big", signed=False))

    def _read_baselines(self, file):
        """
        Read baseline values for the TVOC sensor. Setting the baseline values to the sensor
        makes the measurements available earlier than 12 hours after the sensor was initialized.
        The file is expected to contain 4 bytes - 2 bytes for each baseline value.
        :param file: input file
        :return: tuple of integers - TVOC and CO2 baseline
        """
        with open(file, "rb") as file_obj:
            tvoc_bytes = file_obj.read(2)
            tvoc_baseline = int.from_bytes(tvoc_bytes, byteorder="big")
            co2_bytes = file_obj.read(2)
            co2_baseline = int.from_bytes(co2_bytes, byteorder="big")
            self.logger.debug(
                f"got baselines: TVOC={tvoc_baseline}, CO2={co2_baseline}"
            )

        return tvoc_baseline, co2_baseline

    def _acquire_tvoc_sgp30(self, relative_humidity, temp_celsius):
        """
        :param sgp30_sensor: SGP30 sensor instance
        :param relative_humidity: relative humidity (for calibration)
        :param temp_celsius: temperature (for calibration)
        :return: TVOC ppb
        """

        if relative_humidity and temp_celsius:
            self.logger.debug(
                f"Calibrating the SGP30 sensor with temperature={temp_celsius} "
                f"and relative_humidity={relative_humidity}"
            )
            self.sgp30_sensor.set_iaq_relative_humidity(
                celsius=temp_celsius, relative_humidity=relative_humidity
            )

        tvoc = self.sgp30_sensor.TVOC
        if tvoc and tvoc != 0:  # the initial reading is 0
            self.logger.debug(f"Got TVOC reading from SGP30: {tvoc}")

        try:
            if os.path.exists(self.BASELINE_FILE):
                # Make the baseline values persistent every hour or so.
                baseline_mtime = os.path.getmtime(self.BASELINE_FILE)
                current_time = time.time()
                if baseline_mtime < current_time - 3600:
                    self._write_baselines(self.BASELINE_FILE)
            else:
                self._write_baselines(self.BASELINE_FILE)
        except OSError as exception:
            self.logger.error(
                f"failed to write TVOC baselines to {self.BASELINE_FILE}: {exception}"
            )

        return tvoc

    def _acquire_tvoc_ens160(self, relative_humidity, temp_celsius):
        """
        :param ens160_sensor: ENS160 sensor instance
        :param relative_humidity: relative humidity (for calibration)
        :param temp_celsius: temperature (for calibration)
        :return: TVOC ppb reading or None
        """

        if temp_celsius:
            self.logger.debug(
                f"Calibrating the ENS160 sensor with temperature={temp_celsius}"
            )
            self.ens160_sensor.temperature_compensation = temp_celsius

        if relative_humidity:
            self.logger.debug(
                f"Calibrating the ESP160 sensor with relative_humidity={relative_humidity}"
            )
            self.ens160_sensor.humidity_compensation = relative_humidity

        tvoc = None
        self.logger.debug(f"ENS160 data validity: {self.ens160_sensor.data_validity}")
        if self.ens160_sensor.data_validity == adafruit_ens160.NORMAL_OP:
            tvoc = self.ens160_sensor.TVOC
            self.logger.debug(f"Got TVOC reading from ENS160: {tvoc}")

        return tvoc

    def _acquire_tvoc_sgp41(self, relative_humidity, temp_celsius):
        """
        :param relative_humidity: relative humidity (for calibration)
        :param temp_celsius: temperature (for calibration)
        :return: tuple of VOC index and NOx index readings or None values
        """

        kwargs = {}
        if relative_humidity:
            kwargs["humidity"] = relative_humidity
        if temp_celsius:
            kwargs["temperature"] = temp_celsius

        if time.time() < self.sgp41_conditioning_start + 10:
            self.logger.debug(f"Conditioning the SGP41 sensor with {kwargs}")
            try:
                self.sgp41_sensor.conditioning(**kwargs)
            except (OSError, RuntimeError) as exception:
                self.logger.error(f"failed to condition the SGP41 sensor: {exception}")
            return None, None

        self.logger.debug(f"Calibrating the SGP41 sensor with {kwargs}")
        try:
            tvoc_idx, nox_idx = self.sgp41_sensor.measure_index(**kwargs)
        except (OSError, RuntimeError) as exception:
            self.logger.error(f"failed to get reading from SGP41: {exception}")
            return None, None

        if tvoc_idx == 0:
            tvoc_idx = None
        if nox_idx == 0:
            nox_idx = None
        if tvoc_idx is not None or nox_idx is not None:
            self.logger.debug(
                f"Got readings from SGP41: TVOC index={tvoc_idx}, NOx index={nox_idx}"
            )

        return tvoc_idx, nox_idx

    def get_val(self, relative_humidity, inside_temp):
        """
        retrieve TVOC values
        :return: tuple of TVOC ppb, TVOC index, NOx index readings or None values
        """
        tvoc_ppb = None
        tvoc_idx = None
        nox_idx = None

        if self.sgp30_sensor:
            tvoc_ppb = self._acquire_tvoc_sgp30(relative_humidity, inside_temp)
        if self.ens160_sensor:
            tvoc_ppb = self._acquire_tvoc_ens160(relative_humidity, inside_temp)
        if self.sgp41_sensor:
            tvoc_idx, nox_idx = self._acquire_tvoc_sgp41(relative_humidity, inside_temp)

        return tvoc_ppb, tvoc_idx, nox_idx
