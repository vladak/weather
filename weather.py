#!/usr/bin/env python3
"""
Acquire readings from various sensors and present them via HTTP in Prometheus format.
Some of them are published also to MQTT.
"""

import argparse
import configparser
import json
import logging
import os
import socket
import ssl
import sys
import threading
import time

import adafruit_bmp280
import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_scd4x
import board
from adafruit_minimqtt.adafruit_minimqtt import MMQTTException
from prometheus_api_client import PrometheusConnect
from prometheus_client import Gauge, start_http_server

from logutil import LogLevelAction, get_log_level
from lux import LuxSensor, LuxSensorException
from pm25 import PM25Sensor
from prometheus_util import acquire_prometheus_temperature
from tvoc import TVOCException, TVOCSensor

PRESSURE = "pressure_hpa"
HUMIDITY = "humidity"
LUX = "lux"
CO2 = "co2_ppm"
PM25 = "pm25"
TVOC = "tvoc"
TEMPERATURE = "temperature"


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
    mqtt,
    mqtt_topic,
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

    pm25_sensor = PM25Sensor(i2c)

    lux_sensor = None
    try:
        lux_sensor = LuxSensor(i2c)
    except LuxSensorException as exception:
        logger.error(exception)

    tvoc_sensor = None
    try:
        tvoc_sensor = TVOCSensor(i2c)
    except TVOCException as exception:
        logger.error(exception)

    if scd4x_sensor:
        logger.info("Waiting for the first measurement from the SCD-40 sensor")
        scd4x_sensor.start_periodic_measurement()

    logger.info(f"Connecting to Prometheus on {prometheus_url}")
    prometheus_connect = PrometheusConnect(url=prometheus_url)

    while True:
        mqtt_payload_dict = {}

        # Make sure to stay connected to the broker e.g. in case of keep alive.
        try:
            mqtt.loop(1)
        except MMQTTException as mqtt_exc:
            logger.warning(f"Got MQTT exception: {mqtt_exc}")
            mqtt.reconnect()

        if scd4x_sensor:
            relative_humidity, co2_ppm = acquire_scd4x(scd4x_sensor)
            if co2_ppm:
                mqtt_payload_dict[CO2] = co2_ppm
            if relative_humidity:
                mqtt_payload_dict[HUMIDITY] = relative_humidity

        if lux_sensor:
            lux = lux_sensor.get_value()
            if lux is not None:
                mqtt_payload_dict[LUX] = lux

        #
        # Acquire temperature metrics from OWFS and publish them to MQTT,
        # eah to its own topic.
        # The inside temperature is used for TVOC sensor calibration.
        #
        owfs_temp_dict = acquire_owfs_temperature(owfsdir, temp_sensors)
        inside_temp = None
        logger.debug(f"OWFS temperatures: {owfs_temp_dict}")
        for topic_name, temp_value in owfs_temp_dict.items():
            owfs_sensor_dict = {TEMPERATURE: temp_value}
            logger.debug(f"publishing to {topic_name}: {owfs_sensor_dict}")
            try:
                mqtt.publish(topic_name, json.dumps(owfs_sensor_dict))
            except MMQTTException as mqtt_exc:
                logger.warning(f"Got MQTT exception: {mqtt_exc}")
                mqtt.reconnect()

            if topic_name == temp_inside_name:
                inside_temp = temp_value
                logger.debug(f"inside temperature = {inside_temp}")

        #
        # Acquire outside temperature before pressure so that pressure at sea level
        # can be computed as soon as possible.
        #
        outside_temp = acquire_prometheus_temperature(
            prometheus_connect, temp_outside_name
        )
        logger.debug(f"outside temperature = {outside_temp}")

        if bmp_sensor:
            # Fall back to inside temperature if outside temperature measurement is not available.
            # Assumes the availability of the outside temperature measurement does not flap.
            temp = outside_temp
            if not temp:
                logger.warning(
                    "Falling back to inside temperature for pressure at the sea level calculation"
                )
                temp = inside_temp

            pressure = acquire_pressure(
                bmp_sensor,
                altitude,
                temp,
            )
            if pressure:
                mqtt_payload_dict[PRESSURE] = pressure
                gauges[PRESSURE].labels(name="base").set(pressure)
                if temp:
                    gauges[PRESSURE].labels(name="sea").set(pressure)

        if pm25_sensor:
            data_items = pm25_sensor.get_values()
            if data_items:
                for name, value in data_items:
                    label_name = name.replace(" ", "_")
                    logger.debug(
                        f"setting PM25 gauge with label={label_name} to {value}"
                    )
                    gauges[PM25].labels(measurement=label_name).set(value)

        if tvoc_sensor:
            tvoc_val = tvoc_sensor.get_val(relative_humidity, inside_temp)
            if tvoc_val is not None:
                mqtt_payload_dict[TVOC] = tvoc_val

        if mqtt_payload_dict:
            logger.debug(f"publishing to {mqtt_topic}: {mqtt_payload_dict}")
            try:
                mqtt.publish(mqtt_topic, json.dumps(mqtt_payload_dict))
            except MMQTTException as mqtt_exc:
                logger.warning(f"Got MQTT exception: {mqtt_exc}")
                mqtt.reconnect()

        time.sleep(sleep_timeout)


def acquire_owfs_temperature(owfsdir, temp_sensors):
    """
    Read temperature single temperature value using OWFS.
    :param owfsdir: OWFS directory
    :param temp_sensors: dictionary of ID to name
    :return: dictionary of sensor name to temperature as float value in degrees of Celsius
    """

    logger = logging.getLogger(__name__)

    logger.debug(f"temperature sensors: {dict(temp_sensors.items())}")
    data = {}
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
            data[sensor_name] = float(temp)

    return data


def acquire_pressure(bmp_sensor, altitude, outside_temp):
    """
    Read data from the pressure sensor and calculate pressure at sea level.
    :param bmp_sensor:
    :param altitude: altitude in meters
    :param outside_temp: outside temperature in degrees of Celsius
    :return: pressure value
    """

    logger = logging.getLogger(__name__)

    pressure_val = bmp_sensor.pressure
    if pressure_val and pressure_val > 0:
        logger.debug(f"pressure={pressure_val}")
        if outside_temp:
            pressure_val = sea_level_pressure(pressure_val, outside_temp, altitude)
            logger.debug(f"pressure at sea level={pressure_val}")

    return pressure_val


def acquire_scd4x(scd4x_sensor):
    """
    Reads CO2 and humidity from the SCD4x sensor.
    :param scd4x_sensor:
    :return: tuple of relative humidity and CO2 PPM value
    """

    logger = logging.getLogger(__name__)

    co2_ppm = scd4x_sensor.CO2
    if co2_ppm:
        logger.debug(f"CO2 ppm={co2_ppm}")

    humidity = scd4x_sensor.relative_humidity
    if humidity:
        logger.debug(f"humidity={humidity:.1f}%")

    return humidity, co2_ppm


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
    name of the inside temperature sensor, altitude, Prometheus URL, MQTT broker
    hostname, MQTT topic)
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

    mqtt_broker_name = "mqtt_hostname"
    mqtt_broker = config[global_section_name].get(mqtt_broker_name)
    if not mqtt_broker:
        raise ConfigException(
            f"Section {global_section_name} does not contain {mqtt_broker_name}"
        )

    logger.debug(f"MQTT broker hostname: {mqtt_broker}")

    mqtt_topic_name = "mqtt_topic"
    mqtt_topic = config[global_section_name].get(mqtt_topic_name)
    if not mqtt_topic:
        raise ConfigException(
            f"Section {global_section_name} does not contain {mqtt_topic_name}"
        )

    logger.debug(f"MQTT topic: {mqtt_topic}")

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

    return (
        temp_sensors,
        outside_temp,
        inside_temp,
        altitude,
        prometheus_url,
        mqtt_broker,
        mqtt_topic,
    )


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
            mqtt_hostname,
            mqtt_topic,
        ) = config_load(config, args.config)
    except ConfigException as exc:
        logger.error(f"Failed to process config file: {exc}")
        sys.exit(1)

    #
    # Only some metrics are set in Prometheus directly.
    # The rest is published to MQTT.
    #
    gauges = {
        PRESSURE: Gauge(PRESSURE, "Barometric pressure in hPa", ["name"]),
        PM25: Gauge(PM25, "Particles in air", ["measurement"]),
    }

    logger.debug(f"Gauges: {gauges}")

    if not os.path.isdir(args.owfsdir):
        logger.error(f"Not a directory {args.owfsdir}")
        sys.exit(1)

    mqtt_port = 1883
    mqtt = MQTT.MQTT(
        broker=mqtt_hostname,
        port=mqtt_port,
        socket_pool=socket,
        ssl_context=ssl.create_default_context(),
    )
    logger.info(f"Connecting to MQTT broker {mqtt_hostname} on port {mqtt_port}")
    mqtt.connect()

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
            mqtt,
            mqtt_topic,
        ],
    )
    thread.start()
    thread.join()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
