#!/usr/bin/env python3

"""
Experiment with balancing indoor temperature in a single room.
Basically, when temperature difference between readings from higher and lower placed sensor
rise above certain threshold, a fan is turned on via remotely controlled socket.
After it drops below the threshold, it is turned off.
This assumes certain environment in the room and hence is not universally applicable.
"""

import argparse
import asyncio
import configparser
import logging
import os
import sys
import time
from datetime import datetime

from grafana_client.api import GrafanaApi
from prometheus_api_client import PrometheusConnect

# pylint: disable=no-name-in-module
from tapo import ApiClient

from logutil import LogLevelAction, get_log_level
from prometheus_util import extract_metric_from_data


def parse_args():
    """
    Parse command line arguments
    :return: arguments
    """
    parser = argparse.ArgumentParser(
        description="trun on/off a socket based on temperature difference",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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
        help="Configuration file ",
        default="fan.ini",
    )

    return parser.parse_args()


def get_temperature_difference(url, sensor_a, sensor_b):
    """
    Acquire temperatures and compute their difference.
    :param url: Prometheus URL
    :param sensor_a: name of the sensor that usually reports lower temperature
    :param sensor_b: name of the sensor that usually reports higher temperature
    :return: float value
    """

    logger = logging.getLogger(__name__)

    prometheus_connect = PrometheusConnect(url=url)
    sensor_b = {"sensor": sensor_b}
    sensor_a = {"sensor": sensor_a}
    temp_a = prometheus_connect.get_current_metric_value(
        metric_name="temperature", label_config=sensor_a
    )
    temp_b = prometheus_connect.get_current_metric_value(
        metric_name="temperature", label_config=sensor_b
    )
    logger.debug(f"temp_a = {temp_a} temp_b = {temp_b}")
    diff = float(extract_metric_from_data(temp_b)) - float(
        extract_metric_from_data(temp_a)
    )

    return diff


# pylint: disable=too-few-public-methods
class FanConfig:
    """
    The purpose of this class is to pass all configuration values around.
    Also, to check all config properties are available before entering the loop in main().
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self, config_file):
        logger = logging.getLogger(__name__)

        logger.debug(f"Loading configuration from '{config_file}'")
        config = configparser.ConfigParser()
        with open(config_file, "r", encoding="utf-8") as config_fp:
            config.read_file(config_fp)

        # The extractions purposefully do not use get() in order to generate
        # KeyError exception when a property is missing in the configuration file.
        self.log_level = config["global"]["loglevel"]
        self.hostname = config["global"]["hostname"]
        self.username = config["global"]["username"]
        self.password = config["global"]["password"]
        self.grafana_api_token = config["global"]["grafana_api_token"]
        self.grafana_url = config["global"]["grafana_url"]
        self.dashboard_name = config["global"]["dashboard_name"]
        self.prometheus_url = config["global"]["prometheus_url"]
        self.sensor_a = config["global"]["sensor_a"]
        self.sensor_b = config["global"]["sensor_b"]
        self.sleep_seconds = int(config["global"]["sleep_seconds"])
        # Set to negative value if need to run from midnight.
        self.start_hour = int(config["global"]["start_hour"])
        # Set to 24 if need to run till midnight.
        self.end_hour = int(config["global"]["end_hour"])
        self.temp_diff = int(config["global"]["temp_diff"])


async def main():
    """
    Main loop. Acquire temperature difference from Prometheus,
    set the socket on/off based on the value.
    """
    args = parse_args()

    logging.basicConfig()
    logger = logging.getLogger(__name__)
    logger.setLevel(args.loglevel)

    # To support relative paths.
    os.chdir(os.path.dirname(__file__))

    try:
        config = FanConfig(args.config)
    except OSError as exc:
        logger.error(f"Could not load '{args.config}': {exc}")
        sys.exit(1)
    except KeyError as exc:
        logger.error(f"Missing configuration property: {exc}")
        sys.exit(1)

    config_log_level_str = config.log_level
    if config_log_level_str:
        config_log_level = get_log_level(config_log_level_str)
        if config_log_level:
            logger.setLevel(config_log_level)

    logger.info("Connecting to the plug")
    client = ApiClient(config.username, config.password)
    p110 = await client.p110(config.hostname)
    logger.info("Connected to the plug")

    try:
        await loop(config, p110)
    except KeyboardInterrupt:
        logger.info("Interrupted, turning the fan off")
        await turn_off(p110, config)


def add_grafana_annotation(config, text):
    """
    :param config: configuration loaded from the .ini file
    :param text: text to use
    """
    logger = logging.getLogger(__name__)

    grafana_url = config.grafana_url
    grafana_api = GrafanaApi(auth=config.grafana_api_token, host=grafana_url)
    dashboard_name = config.dashboard_name
    search_results = grafana_api.search.search_dashboards(query=dashboard_name)
    search_result = None
    for result in search_results:
        if result["title"] == dashboard_name:
            search_result = result
            break

    if search_result is not None:
        logger.debug(
            f"Adding Grafana annotation '{text}' to dashboard '{dashboard_name}'"
        )
        grafana_api.annotations.add_annotation(
            text=text, dashboard_id=search_result["id"]
        )
    else:
        logger.error(f"Could not find dashboard with name '{dashboard_name}'")


async def turn_on(p110, config):
    """
    :param p110: PyP100 instance
    :param config: configuration instance
    """
    logger = logging.getLogger(__name__)

    logger.info("Turning on")
    await p110.on()
    add_grafana_annotation(config, "on")


async def turn_off(p110, config):
    """
    :param p110: PyP100 instance
    :param config: configuration instance
    """
    logger = logging.getLogger(__name__)

    logger.info("Turning off")
    await p110.off()
    add_grafana_annotation(config, "off")


async def loop(config, p110):
    """
    :param config: instance of FanConfig
    :param p110: P110 instance to control the socket
    """

    logger = logging.getLogger(__name__)

    sleep_seconds = config.sleep_seconds

    while True:
        temp_diff = get_temperature_difference(
            config.prometheus_url, config.sensor_a, config.sensor_b
        )
        logger.debug(f"Temperature difference {temp_diff}")

        device_info_obj = await p110.get_device_info()
        device_info = device_info_obj.to_dict()
        logger.debug(f"device info: {device_info}")
        device_on = device_info["device_on"]
        logger.debug(f"device_on = {device_on}")

        # Turn off when outside operating hours.
        start_hour = config.start_hour
        end_hour = config.end_hour
        now = datetime.now()
        if now.hour < start_hour or now.hour >= end_hour:
            logger.info("Request received outside of open time window, ignoring")
            if device_on:
                turn_off(p110, config)

            logger.info(f"Sleeping for {sleep_seconds} seconds")
            time.sleep(sleep_seconds)
            continue

        logger.info(f"Temperature difference: {temp_diff}")
        if temp_diff > config.temp_diff:
            if not device_on:
                await turn_on(p110, config)
            else:
                logger.info("Already on")
        else:
            if device_on:
                await turn_off(p110, config)

        logger.info(f"Sleeping for {sleep_seconds} seconds")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
