#!/usr/bin/env python3
"""
Acquire readings from various sensors and present them via HTTP in Prometheus format.
"""

import argparse
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

from logutil import LogLevelAction

KUCHYNE = "kuchyne"
TERASA = "terasa"
PRESSURE = "pressure"
PRESSURE_SEA = "pressure_sea"
HUMIDITY = "humidity"
CO2 = "CO2"
PM25 = "PM25"
temp_sensors = {
    "21F723030000": TERASA,
    "D5F2CF020000": KUCHYNE,
    "E2C0CF020000": "pocitace",
}
sensor_names_to_record = [KUCHYNE, TERASA]


def sea_level_pressure(pressure, outside_temp, height):
    """
    Convert sensor pressure value to value at the sea level.
    The formula uses outside temperature to compensate.
    """
    temp_comp = float(outside_temp) + 273.15
    return pressure / pow(1.0 - 0.0065 * height / temp_comp, 5.255)


def sensor_loop(sleep_timeout, owfsdir, height):
    """
    main loop in which sensor values are collected and set into Prometheus
    client objects.
    """
    logger = logging.getLogger(__name__)

    gauges = {
        KUCHYNE: Gauge("weather_temp_" + KUCHYNE, "Temperature in " + KUCHYNE),
        TERASA: Gauge("weather_temp_" + TERASA, "Temperature in " + TERASA),
        PRESSURE: Gauge("pressure_hpa", "Barometric pressure in hPa"),
        PRESSURE_SEA: Gauge(
            "pressure_sea_level_hpa", "Barometric sea level pressure in hPa"
        ),
        HUMIDITY: Gauge("humidity_pct", "Humidity inside in percent"),
        CO2: Gauge("co2_ppm", "CO2 in ppm"),
        PM25: Gauge("pm25", "Particles in air", ["measurement"]),
    }

    i2c = board.I2C()
    bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)
    scd4x_sensor = adafruit_scd4x.SCD4X(i2c)
    pm25_sensor = PM25_I2C(i2c, None)

    if scd4x_sensor:
        logger.info("Waiting for the first measurement from the SCD-40")
        scd4x_sensor.start_periodic_measurement()

    while True:
        if scd4x_sensor:
            acquire_scd4x(gauges, scd4x_sensor)

        # Acquire temperature before pressure so that pressure at sea level
        # can be computed as soon as possible.
        outside_temp = acquire_temperature(gauges, owfsdir)

        if bmp_sensor:
            acquire_pressure(bmp_sensor, gauges, height, outside_temp)

        acquire_pm25(gauges, pm25_sensor)

        time.sleep(sleep_timeout)


def acquire_pm25(gauges, pm25_sensor):
    """
    Read PM25 data
    :param pm25_sensor:
    :return:
    """

    logger = logging.getLogger(__name__)

    try:
        aqdata = pm25_sensor.read()
    except RuntimeError:
        logger.warning("Unable to read from PM25 sensor")
        return

    logger.debug(f"PM25 data={aqdata}")

    for name, value in aqdata.items():
        label_name = name.replace(" ", "_")
        logger.debug(f"setting PM25 gauge with label={label_name} to {value}")
        gauges[PM25].labels(measurement=label_name).set(value)


def acquire_temperature(gauges, owfsdir):
    """
    Read temperature using OWFS.
    :param gauges:
    :param owfsdir:
    :return: outside temperature
    """

    logger = logging.getLogger(__name__)

    outside_temp = None
    logger.debug(f"temperature sensors: {temp_sensors}")
    for sensor_id, sensor_name in temp_sensors.items():
        file_path = os.path.join(owfsdir, "28." + sensor_id, "temperature")
        with open(file_path, "r", encoding="ascii") as file_obj:
            try:
                temp = file_obj.read()
            except OSError as exception:
                logger.error(f"error while reading {file_path}: {exception}")
                continue

        if temp and sensor_name in sensor_names_to_record:
            logger.debug(f"{sensor_name} temp={temp}")
            gauges[sensor_name].set(temp)

            if sensor_name == TERASA:
                outside_temp = temp

    return outside_temp


def acquire_pressure(bmp_sensor, gauges, height, outside_temp):
    """
    Read data from the pressure sensor and calculate pressure at sea level.
    :param bmp_sensor:
    :param gauges:
    :param height:
    :param outside_temp:
    :return:
    """

    logger = logging.getLogger(__name__)

    pressure_val = bmp_sensor.pressure
    if pressure_val and pressure_val > 0:
        logger.debug(f"pressure={pressure_val}")
        gauges[PRESSURE].set(pressure_val)
        if outside_temp:
            pressure_val = sea_level_pressure(pressure_val, outside_temp, height)
            logger.debug(f"pressure at sea level={pressure_val}")
            gauges[PRESSURE_SEA].set(pressure_val)


def acquire_scd4x(gauges, scd4x_sensor):
    """
    Reads CO2 and humidity from the SCD4x sensor.
    :param gauges:
    :param scd4x_sensor:
    :return:
    """

    logger = logging.getLogger(__name__)

    co2_ppm = scd4x_sensor.CO2
    if co2_ppm:
        logger.debug(f"CO2 ppm={co2_ppm}")
        gauges[CO2].set(co2_ppm)

    humidity = scd4x_sensor.relative_humidity
    if humidity:
        logger.debug(f"humidity={humidity:.1f}%")
        gauges[HUMIDITY].set(humidity)


def main():
    """
    command line run
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
        "-H", "--height", default=245, type=int, help="height for pressure computation"
    )
    parser.add_argument(
        "-l",
        "--loglevel",
        action=LogLevelAction,
        help='Set log level (e.g. "ERROR")',
        default=logging.INFO,
    )
    args = parser.parse_args()

    logging.basicConfig()
    logger = logging.getLogger(__name__)
    logger.setLevel(args.loglevel)
    logger.info("Running")

    if not os.path.isdir(args.owfsdir):
        logger.error(f"Not a directory {args.owfsdir}")
        sys.exit(1)

    logger.info(f"Starting HTTP server on port {args.port}")
    start_http_server(args.port)
    thread = threading.Thread(
        target=sensor_loop, daemon=True, args=[args.sleep, args.owfsdir, args.height]
    )
    thread.start()
    thread.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
