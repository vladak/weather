#!/usr/bin/env python3
"""
Acquire readings from 1-wire temperature sensors and present
them via HTTP for Prometheus server.
"""

import ow
import logging
import time
import sys
import threading

import board
import adafruit_bmp280

from prometheus_client import start_http_server, Gauge


KUCHYNE = 'kuchyne'
TERASA = 'terasa'
PRESSURE = 'pressure'
sensor_names = {'21F723030000': TERASA,
                'D5F2CF020000': KUCHYNE,
                'E2C0CF020000': 'pocitace'}
sensor_names_to_record = [KUCHYNE, TERASA]
EXPOSED_PORT = 8111   # port to listen on for HTTP requests

# It might take some significant time for measurements to be extracted from
# OWFS so even with 1 second the loop will not be tight.
sleep_seconds = 5


def sensor_loop():
    logger = logging.getLogger(__name__)

    ow.init('localhost:4304')
    sensorlist = ow.Sensor('/').sensorList()

    gauges = {KUCHYNE: Gauge('weather_temp_' + KUCHYNE,
                             'Temperature in ' + KUCHYNE),
              TERASA: Gauge('weather_temp_' + TERASA,
                            'Temperature in ' + TERASA),
              PRESSURE: Gauge('pressure_hpa',
                              'Barometric pressure in hPa')}

    i2c = board.I2C()
    bmp_sensor = adafruit_bmp280.Adafruit_BMP280_I2C(i2c)

    # TODO: move this to a thread (to avoid refused connections)
    while True:
        pressure_val = bmp_sensor.pressure
        if pressure_val:
            logger.info(f'pressure={pressure_val}')
            gauges[PRESSURE].set(pressure_val)

        logger.debug("sensors: {}".format(sensorlist))
        for sensor in sensorlist:
            # We only want temperature sensors for now.
            try:
                family = int(sensor.family)
            except ow.exUnknownSensor:
                continue

            if family != 28:
                continue

            sensor_id = sensor.id
            try:
                sensor_name = sensor_names[sensor_id]
            except KeyError:
                sensor_name = sensor_id

            temp = sensor.temperature

            # sometimes 0 value readings are produced
            # - how to tell these are invalid ?
            if temp and sensor_name in sensor_names_to_record:
                logger.info(sensor_name + ' temp=' + temp)
                gauges[sensor_name].set(temp)

        time.sleep(sleep_seconds)


def main():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.info('Running')

    # TODO: callibrate the sensor
    # sensor.sea_level_pressure = 1013.25

    logger.info("Starting HTTP server on port {}".format(EXPOSED_PORT))
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
