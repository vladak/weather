#!/usr/bin/env python3
"""
Acquire readings from 1-wire temperature sensors and present
them via HTTP for Prometheus server.
"""

import argparse
import logging
import os
import sys
import threading
import time

from logutil import LogLevelAction

import adafruit_bmp280
import board
from prometheus_client import Gauge, start_http_server

KUCHYNE = "kuchyne"
TERASA = "terasa"
PRESSURE = "pressure"
PRESSURE_SEA = "pressure_sea"
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
    }

    i2c = board.I2C()
    bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)

    outside_temp = None
    while True:
        if bmp_sensor:
            pressure_val = bmp_sensor.pressure
            if pressure_val:
                logger.info(f"pressure={pressure_val}")
                gauges[PRESSURE].set(pressure_val)
                if outside_temp:
                    pressure_val = sea_level_pressure(
                        pressure_val, outside_temp, int(height)
                    )
                    logger.info(f"pressure at sea level={pressure_val}")
                    gauges[PRESSURE_SEA].set(pressure_val)

        logger.debug(f"sensors: {temp_sensors}")
        for sensor_id, sensor_name in temp_sensors.items():
            with open(
                os.path.join(owfsdir, "28." + sensor_id, "temperature"),
                "r",
                encoding="ascii",
            ) as file_obj:
                temp = file_obj.read()

            # sometimes 0 value readings are produced
            # - how to tell these are invalid ?
            if temp and sensor_name in sensor_names_to_record:
                logger.info(f"{sensor_name} temp={temp}")
                gauges[sensor_name].set(temp)

                if sensor_name == TERASA:
                    outside_temp = temp

        time.sleep(sleep_timeout)


def main():
    """
    command line run
    """
    parser = argparse.ArgumentParser(
        description="weather sensor collector",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--port", default=8111, help="port to listen on for HTTP requests"
    )
    parser.add_argument("--owfsdir", default="/run/owfs", help="OWFS directory")
    parser.add_argument("-s", "--sleep", default=5, help="sleep duration in seconds")
    parser.add_argument(
        "-H", "--height", default=245, help="height for pressure computation"
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
        logger.error(f"Not a directory {OW_PATH_PREFIX}")
        sys.exit(1)

    logger.info(f"Starting HTTP server on port {args.port}")
    start_http_server(int(args.port))
    thread = threading.Thread(target=sensor_loop, daemon=True,
            args=[args.sleep, args.owfsdir, args.height])
    thread.start()
    thread.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
