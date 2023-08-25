#!/usr/bin/env python3
"""

Receive alerts from Grafana and play a mp3 file if the alert
matches a condition.

"""

import argparse
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
from datetime import date, datetime, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pprint import pformat
from shutil import which
from subprocess import TimeoutExpired

import tomli

from logutil import LogLevelAction, get_log_level


class GrafanaAlertHandler(BaseHTTPRequestHandler):
    """
    This class is meant to handle POST requests from Grafana,
    specifically requests to alert.
    """

    def _set_response(self, http_code):
        """
        :param http_code: HTTP response code
        """
        self.send_response(http_code)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

    def do_not_disturb(self, now):
        """
        :param now: datetime instance of current time
        :return: whether alerting should be performed, w.r.t. time allowed
        """
        logger = logging.getLogger(__name__)

        date_today = date(now.year, now.month, now.day)
        start_time = datetime.combine(date_today, time(hour=self.server.start_hr))
        end_time = datetime.combine(date_today, time(hour=self.server.end_hr))

        if now < start_time or now > end_time:
            logger.debug("do not disturb is in effect")
            return True

        return False

    # pylint: disable=invalid-name
    def do_POST(self):
        """
        Handle POST request. In theory requests not matching the expected
        criteria should return "bad request" or such however to keep Grafana
        happy it always returns success.
        """
        logger = logging.getLogger(__name__)

        if not self.headers.get("User-Agent") == "Grafana":
            logger.info("Not a Grafana POST request, ignoring")
            self._set_response(400)
            return

        now = datetime.now()
        if not self.do_not_disturb(now):
            logger.info("Request received outside of open time window, ignoring")
            self._set_response(200)
            return

        content_length = int(self.headers["Content-Length"])
        if content_length == 0:
            logger.info("Empty content, ignoring")
            self._set_response(400)
            return

        post_data = self.rfile.read(content_length)
        if post_data is None:
            logger.info("Empty data, ignoring")
            self._set_response(400)
            return

        data_utf8 = post_data.decode("utf-8")
        try:
            payload = json.loads(data_utf8)
            logger.debug(f"got payload: {pformat(payload)}")
        except json.JSONDecodeError as exc:
            logger.error(f"failed to parse JSON from payload data: {data_utf8}: {exc}")
            self._set_response(400)
            return

        try:
            handle_grafana_payload(
                payload,
                self.server.mp3match,
                self.server.play_queue,
            )
        except GrafanaPayloadException as e:
            logger.error(e)
            self._set_response(400)
            return

        self._set_response(200)
        self.wfile.write(f"POST request for {self.path}".encode("utf-8"))


def play_mp3(play_queue, timeout=30, mpg123="mpg123"):
    """
    Worker to play files in the play_queue via mpg123.
    """

    logger = logging.getLogger(__name__)

    while True:
        path = play_queue.get()
        logger.debug(f"Working on '{path}'")

        if not os.path.exists(path):
            raise OSError(f"file '{path}' does not exist")

        logger.info(f"Playing '{path}'")
        with subprocess.Popen([mpg123, "-q", path]) as proc:
            try:
                _, _ = proc.communicate(timeout=timeout)
            except TimeoutExpired:
                proc.terminate()
                _, _ = proc.communicate()

        logger.debug(f"Finished '{path}'")
        play_queue.task_done()


class GrafanaPayloadException(Exception):
    """
    Trivial class for passing exceptions from handle_grafana_payload().
    """


def handle_grafana_payload(payload, mp3match, play_queue):
    """
    Alerting payload handling. Expects Grafana alert payload (JSON).
    :return True if at least one file was enqueued for playing, False otherwise.
    """

    if payload is None:
        raise GrafanaPayloadException("no payload, ignoring")

    logger = logging.getLogger(__name__)
    logger.debug(f"Got payload: {payload}")

    status = payload.get("status")
    if status is None:
        raise GrafanaPayloadException(f"No status in the alert payload: {payload}")

    alerts = payload.get("alerts")
    if alerts is None:
        raise GrafanaPayloadException(f"No alerts in the alert payload: {payload}")

    queued = False
    for alert in alerts:
        if handle_grafana_alert(alert, mp3match, play_queue):
            queued = True

    return queued


def handle_grafana_alert(alert, mp3match, play_queue):
    """
    Handle single Grafana alert.
    :return True if the file was enqueued for playing, False otherwise.
    """

    logger = logging.getLogger(__name__)

    status = alert.get("status")
    if status is None:
        raise GrafanaPayloadException(f"No status in the alert payload: {alert}")

    if status != "firing":
        logger.debug(f'status not "firing" in the alert payload: {alert}')
        return False

    alert_name = alert.get("labels").get("alertname")
    if alert_name is None:
        raise GrafanaPayloadException(f"No 'alert_name' in alert: {alert}")

    # It should be possible to speed this up by constructing maps of alert name
    # to file and to value (if any), however for now this is good as not many entries
    # are expected in the configuration.
    for file, params in mp3match.items():
        if isinstance(params, list):
            logger.debug(f"Will match {params} on alert name and value")
            alert_name_match = params[0]
            value_match = params[1]
        else:
            logger.debug(f"Will match {params} on alert name")
            alert_name_match = params
            value_match = None

        if alert_name_match == alert_name:
            if value_match is None:
                logger.debug(f"Will play '{file}' based on alert: {alert}")
                play_queue.put(file)
                return True

            alert_value = alert.get("valueString")
            if alert_value and re.match(value_match, alert_value):
                logger.debug(f"Will play '{file}' based on alert: {alert}")
                play_queue.put(file)
                return True

    logger.info(
        f"'alertname' value '{alert_name}' in the alert "
        f"not found in the mappings: {dict(mp3match.items())}"
    )
    return False


class GrafanaAlertHttpServer(HTTPServer):
    """
    Wrapper class to store parameters used by GrafanaAlertHandler.
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        server_address,
        mp3match,
        range_hr,
        play_queue,
        handler_class=GrafanaAlertHandler,
    ):
        super().__init__(server_address, handler_class)
        self.mp3match = mp3match
        start_hr, end_hr = range_hr
        self.start_hr = start_hr
        self.end_hr = end_hr
        self.play_queue = play_queue


def run_server(port, mp3match, range_hr, play_queue):
    """
    Start HTTP server, will not return unless interrupted.
    """
    logger = logging.getLogger(__name__)

    server_address = ("localhost", port)
    httpd = GrafanaAlertHttpServer(server_address, mp3match, range_hr, play_queue)
    logger.info(f"Starting HTTP server on port {port}...")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    httpd.server_close()
    logger.info("Stopping HTTP server...")


def parse_args():
    """
    Parse command line arguments
    """

    parser = argparse.ArgumentParser(
        description="Play a mp3 when Grafana Alert is received via POST req",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p",
        "--port",
        default=8333,
        type=int,
        help="port to listen on for HTTP requests",
    )
    parser.add_argument(
        "-l",
        "--loglevel",
        action=LogLevelAction,
        help='Set log level (e.g. "ERROR")',
        default=logging.INFO,
    )
    parser.add_argument(
        "--mpg123",
        help="Path to the mpg123 executable",
        default="mpg123",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        help="Timeout in seconds to interrupt playing of one mp3",
        default=30,
    )
    parser.add_argument(
        "--config",
        help="Configuration file with mapping from 'alertname' key value "
        "in the Grafana alert payload (exact match) to mp3 file."
        "These should be in the 'name2mp3' section.",
        default="alert.toml",
    )

    return parser.parse_args()


class ConfigException(Exception):
    """
    For passing information about configparser related errors.
    """


def load_mp3_config(config, config_file):
    """
    Load configuration file. Will exit the program on error.
    :return: dictionary with mp3 file mappings
    """

    logger = logging.getLogger(__name__)

    mp3config_section_name = "mp3match"
    if mp3config_section_name not in config.keys():
        raise ConfigException(
            f"Config file {config_file} does not include "
            f"the {mp3config_section_name} section"
        )

    # Check that all mp3 files in the configuration are readable.
    # Of course, this is TOCTOU. play_mp3() will recheck.
    mp3suffix = ".mp3"
    for file, _ in config[mp3config_section_name].items():
        logger.debug(f"Checking file '{file}'")

        if not file.endswith(mp3suffix):
            raise ConfigException(f"File '{file}' does not end with {mp3suffix}")

        try:
            with open(file, "r", encoding="utf-8"):
                pass
        except IOError as exc:
            raise ConfigException(
                f"File '{file}' cannot be opened for reading"
            ) from exc

    logger.debug(f"File mappings: {dict(config[mp3config_section_name].items())}")

    return config[mp3config_section_name]


def load_hr_config(config):
    """
    Load start and end hour from configuration file or return defaults.
    :param config: dictionary representing loaded configuration file
    :return: tuple of start and end hour
    """

    logger = logging.getLogger(__name__)

    start_hr = 8
    end_hr = 23

    section_name = "start_end"
    if section_name in config.keys():
        start_hr_config = config[section_name].get("start_hr")
        if start_hr_config:
            logger.debug(f"Using start hr from config: {start_hr_config}")
            start_hr = start_hr_config

        end_hr_config = config[section_name].get("end_hr")
        if end_hr_config:
            logger.debug(f"Using end hr from config: {end_hr_config}")
            end_hr = end_hr_config

    return start_hr, end_hr


def main():
    """
    command line run
    """
    args = parse_args()
    server_port = args.port

    logging.basicConfig()
    logger = logging.getLogger(__name__)
    logger.setLevel(args.loglevel)

    if which(args.mpg123) is None:
        logger.error("Cannot find mpg123 executable")
        sys.exit(1)

    # To support relative paths.
    os.chdir(os.path.dirname(__file__))

    try:
        with open(args.config, "rb") as config_fp:
            config = tomli.load(config_fp)
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
        mp3match = load_mp3_config(config, args.config)
    except ConfigException as exc:
        logger.error(f"Failed to process config file: {exc}")
        sys.exit(1)

    start_hr, end_hr = load_hr_config(config)
    logger.debug(f"Using range: [{start_hr}, {end_hr}] hours")

    play_queue = queue.Queue()

    threading.Thread(
        target=play_mp3, args=(play_queue, args.timeout, args.mpg123), daemon=True
    ).start()

    run_server(server_port, mp3match, (start_hr, end_hr), play_queue)


if __name__ == "__main__":
    main()
