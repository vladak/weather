#!/usr/bin/env python3
"""
Acquire readings from various sensors and present them via HTTP in Prometheus format.
"""

import argparse
import configparser
import logging
import os
import sys
import threading
import time

import adafruit_bmp280
import adafruit_ens160
import adafruit_scd4x
import adafruit_sgp30
import adafruit_veml7700
import board
from adafruit_pm25.i2c import PM25_I2C
from prometheus_api_client import PrometheusConnect
from prometheus_client import Gauge, start_http_server

from logutil import LogLevelAction, get_log_level
from prometheus_util import acquire_prometheus_temperature

PRESSURE = "pressure"
HUMIDITY = "humidity"
LUX = "Lux"
CO2 = "CO2"
PM25 = "PM25"
TVOC = "TVOC"
TEMPERATURE = "temperature"

BASELINE_FILE = "tvoc_baselines.dat"


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


# pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements,too-many-positional-arguments
def sensor_loop(
    sleep_timeout,
    owfsdir,
    altitude,
    temp_sensors,
    temp_outside_name,
    temp_inside_name,
    gauges,
    prometheus_url,
):
    """
    main loop in which sensor values are collected and set into Prometheus
    client objects.
    """
    logger = logging.getLogger(__name__)

    i2c = board.I2C()

    try:
        bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)
        logger.info("BMP280 sensor connected")
    except RuntimeError as exception:
        logger.error(f"cannot instantiate BMP280 sensor: {exception}")
        bmp_sensor = None

    scd4x_sensor = None
    try:
        scd4x_sensor = adafruit_scd4x.SCD4X(i2c)
        logger.info("SCD4x sensor connected")
    except ValueError as exception:
        logger.error(f"cannot find SCD4x sensor: {exception}")

    pm25_sensor = PM25_I2C(i2c, None)

    try:
        veml7700_sensor = adafruit_veml7700.VEML7700(i2c)
        logger.info("VEML7700 sensor connected")
    except RuntimeError as exception:
        logger.error(f"cannot instantiate VEML7700 sensor: {exception}")
        veml7700_sensor = None

    try:
        ens160_sensor = adafruit_ens160.ENS160(i2c)
        logger.info(
            f"ENS160 sensor present (firmware {ens160_sensor.firmware_version})"
        )
    except (ValueError, RuntimeError) as exception:
        logger.error(f"cannot instantiate ENS160 sensor: {exception}")
        ens160_sensor = None

    # Try to fall back to SGP30 if ENS160 is not present or cannot be instantiated.
    sgp30_sensor = None
    if ens160_sensor is None:
        try:
            sgp30_sensor = adafruit_sgp30.Adafruit_SGP30(i2c)
            try:
                tvoc_baseline, co2_baseline = read_baselines(BASELINE_FILE)
                sgp30_sensor.set_iaq_baseline(co2_baseline, tvoc_baseline)
            except OSError as exception:
                logger.error(
                    f"failed to get baselines for the SGP30 sensor: {exception}"
                )
        except (OSError, RuntimeError) as exception:
            logger.error(f"cannot instantiate SGP30 sensor: {exception}")
            sgp30_sensor = None

    if scd4x_sensor:
        logger.info("Waiting for the first measurement from the SCD-40 sensor")
        scd4x_sensor.start_periodic_measurement()

    prometheus_connect = PrometheusConnect(url=prometheus_url)

    while True:
        relative_humidity = None

        if scd4x_sensor:
            relative_humidity = acquire_scd4x(
                gauges[CO2], gauges[HUMIDITY], scd4x_sensor, temp_inside_name
            )

        if veml7700_sensor:
            acquire_light(gauges[LUX], veml7700_sensor, temp_inside_name)

        # Acquire outside temperature before pressure so that pressure at sea level
        # can be computed as soon as possible.
        # Similarly, the inside temperature is used for TVOC sensor calibration.
        inside_temp = acquire_owfs_temperature(
            gauges[TEMPERATURE], owfsdir, temp_sensors, temp_inside_name
        )

        outside_temp = acquire_prometheus_temperature(
            prometheus_connect, temp_outside_name
        )

        if bmp_sensor:
            # Fall back to inside temperature if outside temperature measurement is not available.
            # Assumes the availability of the outside temperature measurement does not flap.
            temp = outside_temp
            if not temp:
                logger.warning(
                    "Falling back to inside temperature for pressure at the sea level calculation"
                )
                temp = inside_temp

            acquire_pressure(
                bmp_sensor,
                gauges[PRESSURE],
                altitude,
                temp,
            )

        if pm25_sensor:
            acquire_pm25(gauges[PM25], pm25_sensor)

        if sgp30_sensor:
            acquire_tvoc_sgp30(
                gauges[TVOC], sgp30_sensor, relative_humidity, inside_temp
            )
        if ens160_sensor:
            acquire_tvoc_ens160(
                gauges[TVOC], ens160_sensor, relative_humidity, inside_temp
            )

        time.sleep(sleep_timeout)


def write_baselines(sgp30_sensor, file):
    """
    :param sgp30_sensor: sensor instance
    :param file: output file
    """
    logger = logging.getLogger(__name__)

    tvoc_baseline = sgp30_sensor.baseline_TVOC
    co2_baseline = sgp30_sensor.baseline_eCO2

    if tvoc_baseline != 0 and co2_baseline != 0:
        logger.debug(
            f"writing baselines to {file}: TVOC={tvoc_baseline}, CO2={co2_baseline}"
        )

        with open(file, "wb") as file_obj:
            file_obj.write(tvoc_baseline.to_bytes(2, byteorder="big", signed=False))
            file_obj.write(co2_baseline.to_bytes(2, byteorder="big", signed=False))


def read_baselines(file):
    """
    Read baseline values for the TVOC sensor. Setting the baseline values to the sensor
    makes the measurements available earlier than 12 hours after the sensor was initialized.
    The file is expected to contain 4 bytes - 2 bytes for each baseline value.
    :param file: input file
    :return: tuple of integers - TVOC and CO2 baseline
    """
    logger = logging.getLogger(__name__)

    with open(file, "rb") as file_obj:
        tvoc_bytes = file_obj.read(2)
        tvoc_baseline = int.from_bytes(tvoc_bytes, byteorder="big")
        co2_bytes = file_obj.read(2)
        co2_baseline = int.from_bytes(co2_bytes, byteorder="big")
        logger.debug(f"got baselines: TVOC={tvoc_baseline}, CO2={co2_baseline}")

    return tvoc_baseline, co2_baseline


def acquire_tvoc_sgp30(gauge, sgp30_sensor, relative_humidity, temp_celsius):
    """
    :param gauge: Gauge object
    :param sgp30_sensor: SGP30 sensor instance
    :param relative_humidity: relative humidity (for calibration)
    :param temp_celsius: temperature (for calibration)
    :return: TVOC
    """

    logger = logging.getLogger(__name__)

    if relative_humidity and temp_celsius:
        logger.debug(
            f"Calibrating the SGP30 sensor with temperature={temp_celsius} "
            f"and relative_humidity={relative_humidity}"
        )
        sgp30_sensor.set_iaq_relative_humidity(
            celsius=temp_celsius, relative_humidity=relative_humidity
        )

    tvoc = sgp30_sensor.TVOC
    if tvoc and tvoc != 0:  # the initial reading is 0
        logger.debug(f"Got TVOC reading from SGP30: {tvoc}")
        gauge.set(tvoc)

    try:
        if os.path.exists(BASELINE_FILE):
            # Make the baseline values persistent every hour or so.
            baseline_mtime = os.path.getmtime(BASELINE_FILE)
            current_time = time.time()
            if baseline_mtime < current_time - 3600:
                write_baselines(sgp30_sensor, BASELINE_FILE)
        else:
            write_baselines(sgp30_sensor, BASELINE_FILE)
    except OSError as exception:
        logger.error(f"failed to write TVOC baselines to {BASELINE_FILE}: {exception}")

    return tvoc


def acquire_tvoc_ens160(gauge, ens160_sensor, relative_humidity, temp_celsius):
    """
    :param gauge: Gauge object
    :param ens160_sensor: ENS160 sensor instance
    :param relative_humidity: relative humidity (for calibration)
    :param temp_celsius: temperature (for calibration)
    :return: TVOC reading or None
    """

    logger = logging.getLogger(__name__)

    if temp_celsius:
        logger.debug(f"Calibrating the ENS160 sensor with temperature={temp_celsius}")
        ens160_sensor.temperature_compensation = temp_celsius

    if relative_humidity:
        logger.debug(
            f"Calibrating the ESP160 sensor with relative_humidity={relative_humidity}"
        )
        ens160_sensor.humidity_compensation = relative_humidity

    tvoc = None
    logger.debug(f"ENS160 data validity: {ens160_sensor.data_validity}")
    if ens160_sensor.data_validity == adafruit_ens160.NORMAL_OP:
        tvoc = ens160_sensor.TVOC
        logger.debug(f"Got TVOC reading from ENS160: {tvoc}")
        gauge.set(tvoc)

    return tvoc


def acquire_pm25(gauge, pm25_sensor):
    """
    Read PM25 data
    :param gauge Gauge object
    :param pm25_sensor: PM25 sensor object
    :return:
    """

    logger = logging.getLogger(__name__)

    try:
        acquired_data = pm25_sensor.read()
    except RuntimeError:
        logger.warning("Unable to read from PM25 sensor")
        return

    logger.debug(f"PM25 data={acquired_data}")

    for name, value in acquired_data.items():
        label_name = name.replace(" ", "_")
        logger.debug(f"setting PM25 gauge with label={label_name} to {value}")
        gauge.labels(measurement=label_name).set(value)


def acquire_owfs_temperature(gauge, owfsdir, temp_sensors, temp_name):
    """
    Read temperature single temperature value using OWFS.
    :param gauge: Gauge object
    :param owfsdir: OWFS directory
    :param temp_sensors: dictionary of ID to name
    :param temp_name: name of the temperature sensor
    :return: temperature as float value in degrees of Celsius
    """

    logger = logging.getLogger(__name__)

    temp_value = None
    logger.debug(f"temperature sensors: {dict(temp_sensors.items())}")
    for sensor_id, sensor_name in temp_sensors.items():
        file_path = os.path.join(owfsdir, "28." + sensor_id, "temperature")
        try:
            with open(file_path, "r", encoding="ascii") as file_obj:
                temp = file_obj.read()
        except OSError as exception:
            logger.error(f"error while reading '{file_path}': {exception}")
            continue

        if temp:
            logger.debug(f"{sensor_name} temp={temp}")
            gauge.labels(sensor=sensor_name).set(temp)

            if sensor_name == temp_name:
                temp_value = float(temp)
                break

    return temp_value


def acquire_pressure(bmp_sensor, gauge_pressure, altitude, outside_temp):
    """
    Read data from the pressure sensor and calculate pressure at sea level.
    :param bmp_sensor:
    :param gauge_pressure: Gauge object
    :param altitude: altitude in meters
    :param outside_temp: outside temperature in degrees of Celsius
    :return:
    """

    logger = logging.getLogger(__name__)

    pressure_val = bmp_sensor.pressure
    if pressure_val and pressure_val > 0:
        logger.debug(f"pressure={pressure_val}")
        gauge_pressure.labels(name="base").set(pressure_val)
        if outside_temp:
            pressure_val = sea_level_pressure(pressure_val, outside_temp, altitude)
            logger.debug(f"pressure at sea level={pressure_val}")
            gauge_pressure.labels(name="sea").set(pressure_val)


def acquire_scd4x(gauge_co2, gauge_humidity, scd4x_sensor, location_name):
    """
    Reads CO2 and humidity from the SCD4x sensor.
    :param gauge_co2: Gauge object
    :param gauge_humidity: Gauge object
    :param scd4x_sensor:
    :param location_name: name of the location. Used for tagging.
    :return: relative humidity
    """

    logger = logging.getLogger(__name__)

    co2_ppm = scd4x_sensor.CO2
    if co2_ppm:
        logger.debug(f"CO2 ppm={co2_ppm}")
        gauge_co2.labels(location=location_name).set(co2_ppm)

    humidity = scd4x_sensor.relative_humidity
    if humidity:
        logger.debug(f"humidity={humidity:.1f}%")
        gauge_humidity.labels(location=location_name).set(humidity)

    return humidity


def acquire_light(gauge_lux, light_sensor, location_name):
    """
    Reads light amount in the form of Lux
    :param gauge_lux Gauge object
    :param light_sensor light sensor object
    :param location_name: name of the location. Used for tagging.
    :return:
    """

    logger = logging.getLogger(__name__)

    lux = light_sensor.light
    if lux:
        logger.debug(f"lux={lux}")
        gauge_lux.labels(location=location_name).set(lux)


def parse_args():
    """
    Command line options parsing
    :return:
    """
    parser = argparse.ArgumentParser(
        description="weather sensor collector",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p",
        "--port",
        default=8111,
        type=int,
        help="port to listen on for HTTP requests",
    )
    parser.add_argument("--owfsdir", default="/run/owfs", help="OWFS directory")
    parser.add_argument(
        "-s", "--sleep", default=5, type=int, help="sleep duration in seconds"
    )
    parser.add_argument(
        "-l",
        "--loglevel",
        action=LogLevelAction,
        help='Set log level (e.g. "ERROR")',
        default=logging.INFO,
    )
    parser.add_argument(
        "--config",
        help="Configuration file",
        default="weather.ini",
    )

    return parser.parse_args()


class ConfigException(Exception):
    """
    For passing information about configparser related errors.
    """


def conf_get_altitude(config, global_section_name):
    """
    :param config: configparser instance
    :param global_section_name: name of the global section
    :return: altitude value (int)
    """
    logger = logging.getLogger(__name__)

    altitude_name = "altitude"
    altitude_value = config[global_section_name].get(altitude_name)
    if not altitude_value:
        raise ConfigException(
            f"Section {global_section_name} does not contain {altitude_name}"
        )

    try:
        altitude = int(altitude_value)
    except ValueError as exc:
        raise ConfigException(
            f"Altitude value is not an integer: {altitude_value}"
        ) from exc

    logger.debug(f"Altitude = {altitude}")
    return altitude


def config_load(config, config_file):
    """
    Load temperature sensor information. Will exit the program on failure.
    :param config: configparser instance
    :param config_file: configuration file (for logging)
    :return: (dictionary of 1-wire ID to name, name of outside temperature sensor,
    name of the inside temperature sensor, altitude, Prometheus URL)
    """

    logger = logging.getLogger(__name__)

    temp_sensors_section_name = "temp_sensors"
    if temp_sensors_section_name not in config.sections():
        raise ConfigException(
            f"Config file {config_file} does not include "
            f"the {temp_sensors_section_name} section"
        )

    temp_sensors = config[temp_sensors_section_name]
    logger.debug(f"Temperature sensor mappings: {dict(temp_sensors.items())}")

    global_section_name = "global"
    if global_section_name not in config.sections():
        raise ConfigException(
            f"Config file {config_file} does not include "
            f"the {global_section_name} section"
        )

    prometheus_url_name = "prometheus_url"
    prometheus_url = config[global_section_name].get(prometheus_url_name)
    if not prometheus_url:
        raise ConfigException(
            f"Section {global_section_name} does not contain {prometheus_url_name}"
        )

    logger.debug(f"Prometheus URL: {prometheus_url}")

    outside_temp_name = "outside_temp_name"
    outside_temp = config[global_section_name].get(outside_temp_name)
    if not outside_temp:
        raise ConfigException(
            f"Section {global_section_name} does not contain {outside_temp_name}"
        )

    logger.debug(f"outside temperature sensor: {outside_temp}")

    inside_temp_name = "inside_temp_name"
    inside_temp = config[global_section_name].get(inside_temp_name)
    if not inside_temp:
        raise ConfigException(
            f"Section {global_section_name} does not contain {inside_temp_name}"
        )

    logger.debug(f"inside temperature sensor: {inside_temp}")

    if inside_temp not in temp_sensors.values():
        raise ConfigException(
            f"name of inside temperature sensor ({inside_temp_name}) "
            f"not present in temperature sensors: {temp_sensors}"
        )

    altitude = conf_get_altitude(config, global_section_name)

    return temp_sensors, outside_temp, inside_temp, altitude, prometheus_url


def main():
    """
    command line run
    """
    args = parse_args()

    logging.basicConfig()
    logger = logging.getLogger(__name__)
    logger.setLevel(args.loglevel)
    logger.info("Running")

    # To support relative paths.
    os.chdir(os.path.dirname(__file__))

    config = configparser.ConfigParser()
    try:
        with open(args.config, "r", encoding="utf-8") as config_fp:
            config.read_file(config_fp)
    except OSError as exc:
        logger.error(f"Could not load '{args.config}': {exc}")
        sys.exit(1)

    # Log level from configuration overrides command line option.
    config_log_level_str = config["global"].get("loglevel")
    if config_log_level_str:
        config_log_level = get_log_level(config_log_level_str)
        if config_log_level:
            logger.setLevel(config_log_level)

    try:
        (
            temp_sensors,
            temp_outside_name,
            temp_inside_name,
            altitude,
            prometheus_url,
        ) = config_load(config, args.config)
    except ConfigException as exc:
        logger.error(f"Failed to process config file: {exc}")
        sys.exit(1)

    gauges = {
        PRESSURE: Gauge("pressure_hpa", "Barometric pressure in hPa", ["name"]),
        HUMIDITY: Gauge("humidity_pct", "Relative humidity in percent", ["location"]),
        CO2: Gauge("co2_ppm", "CO2 in ppm", ["location"]),
        PM25: Gauge("pm25", "Particles in air", ["measurement"]),
        LUX: Gauge("lux", "Light in Lux units", ["location"]),
        TVOC: Gauge("tvoc", "Total Volatile Organic Compounds"),
        TEMPERATURE: Gauge(
            "temperature", "temperature in degrees of Celsius", ["sensor"]
        ),
    }

    logger.debug(f"Gauges: {gauges}")

    if not os.path.isdir(args.owfsdir):
        logger.error(f"Not a directory {args.owfsdir}")
        sys.exit(1)

    logger.info(f"Starting HTTP server on port {args.port}")
    start_http_server(args.port)
    thread = threading.Thread(
        target=sensor_loop,
        daemon=True,
        args=[
            args.sleep,
            args.owfsdir,
            altitude,
            temp_sensors,
            temp_outside_name,
            temp_inside_name,
            gauges,
            prometheus_url,
        ],
    )
    thread.start()
    thread.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
