#!/usr/bin/env python3
"""
Acquire readings from 1-wire temperature sensors and present
them via HTTP for Prometheus server.
"""

import logging
import time
import os
import sys
import threading

import board
import adafruit_bmp280

from prometheus_client import start_http_server, Gauge


KUCHYNE = 'kuchyne'
TERASA = 'terasa'
PRESSURE = 'pressure'
PRESSURE_SEA = 'pressure_sea'
temp_sensors = {'21F723030000': TERASA,
                'D5F2CF020000': KUCHYNE,
                'E2C0CF020000': 'pocitace'}
sensor_names_to_record = [KUCHYNE, TERASA]
EXPOSED_PORT = 8111   # port to listen on for HTTP requests

# It might take some significant time for measurements to be extracted from
# OWFS so even with 1 second the loop will not be tight.
SLEEP_SECONDS = 5

OW_PATH_PREFIX = '/run/owfs'

HEIGHT = 245


def sea_level_pressure(pressure, outside_temp, height):
    """
    Convert sensor pressure value to value at the sea level.
    The formula uses outside temperature to compensate.
    """
    temp_comp = float(outside_temp) + 273.15
    return pressure / pow(1.0 - 0.0065 * height / temp_comp, 5.255)


def sensor_loop():
    """
    main loop in which sensor values are collected and set into Prometheus
    client objects.
    """
    logger = logging.getLogger(__name__)

    gauges = {KUCHYNE: Gauge('weather_temp_' + KUCHYNE,
                             'Temperature in ' + KUCHYNE),
              TERASA: Gauge('weather_temp_' + TERASA,
                            'Temperature in ' + TERASA),
              PRESSURE: Gauge('pressure_hpa',
                              'Barometric pressure in hPa'),
              PRESSURE_SEA: Gauge('pressure_sea_level_hpa',
                                  'Barometric sea level pressure in hPa')}

    i2c = board.I2C()
    bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)

    outside_temp = None
    while True:
        if bmp_sensor:
            pressure_val = bmp_sensor.pressure
            if pressure_val:
                logger.info(f'pressure={pressure_val}')
                gauges[PRESSURE].set(pressure_val)
                if outside_temp:
                    pressure_val = sea_level_pressure(pressure_val,
                                                      outside_temp, HEIGHT)
                    logger.info(f'pressure at sea level={pressure_val}')
                    gauges[PRESSURE_SEA].set(pressure_val)

        logger.debug(f"sensors: {temp_sensors}")
        for sensor_id, sensor_name in temp_sensors.items():
            with open(os.path.join(OW_PATH_PREFIX,
                                   '28.' + sensor_id,
                                   'temperature'), "r",
                      encoding='ascii') as file_obj:
                temp = file_obj.read()

            # sometimes 0 value readings are produced
            # - how to tell these are invalid ?
            if temp and sensor_name in sensor_names_to_record:
                logger.info(f'{sensor_name} temp={temp}')
                gauges[sensor_name].set(temp)

                if sensor_name == TERASA:
                    outside_temp = temp

        time.sleep(SLEEP_SECONDS)


def main():
    """
    command line run
    """
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.info('Running')

    if not os.path.isdir(OW_PATH_PREFIX):
        logger.error("Not a directory {}".format(OW_PATH_PREFIX))
        sys.exit(1)

    logger.info(f"Starting HTTP server on port {EXPOSED_PORT}")
    start_http_server(EXPOSED_PORT)
    thread = threading.Thread(target=sensor_loop, daemon=True)
    thread.start()
    thread.join()


if __name__ == "__main__":
    logging.basicConfig()
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
