#!/usr/bin/env python3
"""
Acquire readings from 1-wire temperature sensors and log them into
a log file so they can be read from there using Telegraf and passed
to InfluxDB which will be used as data source in Grafana.
"""

import ow
import logging
import time
import sys


sensor_names = {'21F723030000': 'terasa',
                'D5F2CF020000': 'kuchyne',
                'E2C0CF020000': 'pocitace'}
# The order needs to be preserved.
sensor_names_to_record = ['kuchyne', 'terasa']
TELEGRAF_SEPARATOR = "telegraf: "
LOG_FILE = '/run/user/1000/temperature.log'


def get_logger(name=__name__, handler=logging.StreamHandler(),
               level=logging.INFO):
    """
    :param name: logger name
    :param level: log level
    :return: logger
    """
    format = '%(asctime)s %(levelname)8s %(name)s | %(message)s'

    logger = logging.getLogger(name)
    logger.setLevel(level)
    # Use ISO 8601 date format.
    formatter = logging.Formatter(format, datefmt='%Y-%m-%d %H:%M:%S%z')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def main():
    sleep_seconds = 1

    if sys.stdin.isatty():
        logger = get_logger()
    else:
        logger = get_logger(handler=logging.NullHandler())
    logger.debug('Running')

    file_logger = get_logger(name='weather',
                             handler=logging.
                             FileHandler(LOG_FILE))

    ow.init('localhost:4304')
    sensorlist = ow.Sensor('/').sensorList()

    while True:
        record = {}
        for sensor in sensorlist:
            # We only want temperature sensors for now.
            if int(sensor.family) != 28:
                continue

            sensor_id = sensor.id
            try:
                sensor_name = sensor_names[sensor_id]
            except KeyError:
                sensor_name = sensor_id

            temp = sensor.temperature
            logger.info(sensor_name + ' temp=' + temp)

            if sensor_name in sensor_names_to_record:
                record[sensor_name] = temp

            time.sleep(sleep_seconds)

        if len(record) == len(sensor_names_to_record):
            value_str = ""
            for name in sensor_names_to_record:
                value_str = value_str + f"{name}={record[name]} "

            file_logger.info(TELEGRAF_SEPARATOR + value_str)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
