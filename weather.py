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
import adafruit_scd4x
import board
from adafruit_pm25.i2c import PM25_I2C
from prometheus_client import Gauge, start_http_server

from logutil import LogLevelAction, get_log_level

PRESSURE = "pressure"
PRESSURE_SEA = "pressure_sea"
HUMIDITY = "humidity"
CO2 = "CO2"
PM25 = "PM25"


def sea_level_pressure(pressure, outside_temp, altitude):
    """
    Convert sensor pressure value to value at the sea level.
    The formula uses outside temperature to compensate.
    """
    temp_comp = float(outside_temp) + 273.15
    return pressure / pow(1.0 - 0.0065 * int(altitude) / temp_comp, 5.255)


# pylint: disable=too-many-arguments
def sensor_loop(
    sleep_timeout, owfsdir, altitude, temp_sensors, temp_outside_name, gauges
):
    """
    main loop in which sensor values are collected and set into Prometheus
    client objects.
    """
    logger = logging.getLogger(__name__)

    i2c = board.I2C()
    bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)
    scd4x_sensor = adafruit_scd4x.SCD4X(i2c)
    pm25_sensor = PM25_I2C(i2c, None)

    if scd4x_sensor:
        logger.info("Waiting for the first measurement from the SCD-40 sensor")
        scd4x_sensor.start_periodic_measurement()

    while True:
        if scd4x_sensor:
            acquire_scd4x(gauges[CO2], gauges[HUMIDITY], scd4x_sensor)

        # Acquire temperature before pressure so that pressure at sea level
        # can be computed as soon as possible.
        outside_temp = acquire_temperature(
            gauges, owfsdir, temp_sensors, temp_outside_name
        )

        if bmp_sensor:
            acquire_pressure(
                bmp_sensor,
                gauges[PRESSURE],
                gauges[PRESSURE_SEA],
                altitude,
                outside_temp,
            )

        if pm25_sensor:
            acquire_pm25(gauges[PM25], pm25_sensor)

        time.sleep(sleep_timeout)


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


def acquire_temperature(gauges, owfsdir, temp_sensors, temp_outside_name):
    """
    Read temperature using OWFS.
    :param gauges:
    :param owfsdir: OWFS directory
    :param temp_sensors: dictionary of ID to name
    :param temp_outside_name: name of the outside temperature sensor
    :return: outside temperature
    """

    logger = logging.getLogger(__name__)

    outside_temp = None
    logger.debug(f"temperature sensors: {dict(temp_sensors.items())}")
    for sensor_id, sensor_name in temp_sensors.items():
        file_path = os.path.join(owfsdir, "28." + sensor_id, "temperature")
        with open(file_path, "r", encoding="ascii") as file_obj:
            try:
                temp = file_obj.read()
            except OSError as exception:
                logger.error(f"error while reading {file_path}: {exception}")
                continue

        if temp:
            logger.debug(f"{sensor_name} temp={temp}")
            gauges[sensor_name].set(temp)

            if sensor_name == temp_outside_name:
                outside_temp = temp

    return outside_temp


def acquire_pressure(
    bmp_sensor, gauge_pressure, gauge_pressure_sea, height, outside_temp
):
    """
    Read data from the pressure sensor and calculate pressure at sea level.
    :param bmp_sensor:
    :param gauge_pressure: Gauge object
    :param gauge_pressure_sea: Gauge object
    :param height:
    :param outside_temp:
    :return:
    """

    logger = logging.getLogger(__name__)

    pressure_val = bmp_sensor.pressure
    if pressure_val and pressure_val > 0:
        logger.debug(f"pressure={pressure_val}")
        gauge_pressure.set(pressure_val)
        if outside_temp:
            pressure_val = sea_level_pressure(pressure_val, outside_temp, height)
            logger.debug(f"pressure at sea level={pressure_val}")
            gauge_pressure_sea.set(pressure_val)


def acquire_scd4x(gauge_co2, gauge_humidity, scd4x_sensor):
    """
    Reads CO2 and humidity from the SCD4x sensor.
    :param gauge_co2: Gauge object
    :param gauge_humidity: Gauge object
    :param scd4x_sensor:
    :return:
    """

    logger = logging.getLogger(__name__)

    co2_ppm = scd4x_sensor.CO2
    if co2_ppm:
        logger.debug(f"CO2 ppm={co2_ppm}")
        gauge_co2.set(co2_ppm)

    humidity = scd4x_sensor.relative_humidity
    if humidity:
        logger.debug(f"humidity={humidity:.1f}%")
        gauge_humidity.set(humidity)


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


def config_load(config, config_file):
    """
    Load temperature sensor information. Will exit the program on failure.
    :param config: configparser instance
    :param config_file: configuration file (for logging)
    :return: (dictionary of 1-wire ID to name, name of outside temperature sensor, altitude)
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

    outside_temp_name = "outside_temp_name"
    outside_temp = config[global_section_name].get(outside_temp_name)
    if not outside_temp:
        raise ConfigException(
            f"Section {global_section_name} does not contain {outside_temp_name}"
        )

    logger.debug(f"outside temperature sensor: {outside_temp}")

    if outside_temp not in temp_sensors.values():
        raise ConfigException(
            f"name of outside temperature sensor ({outside_temp_name}) "
            f"not present in temperature sensors: {temp_sensors}"
        )

    altitude_name = "altitude"
    try:
        altitude = int(config[global_section_name].get(altitude_name))
    except ValueError as exc:
        raise ConfigException(
            f"Section {global_section_name} does not contain {altitude_name}"
        ) from exc

    logger.debug(f"Altitude = {altitude}")

    return temp_sensors, outside_temp, altitude


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
        temp_sensors, temp_outside_name, altitude = config_load(config, args.config)
    except ConfigException as exc:
        logger.error(f"Failed to process config file: {exc}")
        sys.exit(1)

    gauges = {
        PRESSURE: Gauge("pressure_hpa", "Barometric pressure in hPa"),
        PRESSURE_SEA: Gauge(
            "pressure_sea_level_hpa", "Barometric sea level pressure in hPa"
        ),
        HUMIDITY: Gauge("humidity_pct", "Humidity inside in percent"),
        CO2: Gauge("co2_ppm", "CO2 in ppm"),
        PM25: Gauge("pm25", "Particles in air", ["measurement"]),
    }

    for temp_sensor_name in temp_sensors.values():
        gauges[temp_sensor_name] = Gauge(
            "weather_temp_" + temp_sensor_name, "Temperature in " + temp_sensor_name
        )

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
            gauges,
        ],
    )
    thread.start()
    thread.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
