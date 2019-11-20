#!/usr/bin/env python3
"""
Acquire readings from 1-wire temperature sensors and present
them via HTTP for Prometheus server.
"""

import ow
import logging
import time
import sys
from prometheus_client import start_http_server, Gauge


KUCHYNE = 'kuchyne'
TERASA = 'terasa'
sensor_names = {'21F723030000': TERASA,
                'D5F2CF020000': KUCHYNE,
                'E2C0CF020000': 'pocitace'}
sensor_names_to_record = [KUCHYNE, TERASA]
EXPOSED_PORT = 8111   # port to listen on for HTTP requests


def main():
    # It might take some significant time for measurements to be extracted from
    # OWFS so even with 1 second the loop will not be tight.
    sleep_seconds = 5

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.debug('Running')

    ow.init('localhost:4304')
    sensorlist = ow.Sensor('/').sensorList()

    gauges = {KUCHYNE: Gauge('weather_temp_' + KUCHYNE,
                             'Temperature in ' + KUCHYNE),
              TERASA: Gauge('weather_temp_' + TERASA,
                            'Temperature in ' + TERASA)}

    start_http_server(EXPOSED_PORT)

    while True:
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
            logger.info(sensor_name + ' temp=' + temp)

            if sensor_name in sensor_names_to_record:
                gauges[sensor_name].set(temp)

            time.sleep(sleep_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
