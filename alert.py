#!/usr/bin/env python3
"""

Receive alerts from Grafana and play a mp3 file if the alert
matches a condition.

"""

import argparse
import configparser
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pprint import pformat
from shutil import which
from subprocess import TimeoutExpired

from logutil import LogLevelAction


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
        if now.hour < self.server.start_hr or now.hour > self.server.end_hr:
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
            handle_grafana_alert(
                payload,
                self.server.rule2file,
                self.server.play_queue,
            )
        except GrafanaPayloadException:
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
    Trivial class for passing exceptions from handle_grafana_alert().
    """


def handle_grafana_alert(payload, rule2file, play_queue):
    """
    Alert handling. Expects Grafana alert payload (JSON).
    :return True if the file was enqueued for playing, False otherwise.
    """

    logger = logging.getLogger(__name__)

    if payload is None:
        logger.error("no payload, ignoring")
        raise GrafanaPayloadException()

    state = payload.get("state")
    if state is None:
        logger.error(f"No state in the alert payload: {payload}")
        raise GrafanaPayloadException()

    # Technically, "pending" state counts too, however playing the sound
    # too often might be too obnoxious.
    if state != "alerting":
        logger.info(f"state not alerting in the alert payload: {payload}")
        return False

    rule_name = payload.get("ruleName")
    if rule_name is None:
        logger.error(f"No 'ruleName' in payload: {payload}")
        raise GrafanaPayloadException()

    file_to_play = rule2file.get(rule_name)
    if not file_to_play:
        logger.error(
            f"'ruleName' value '{rule_name}' in the payload "
            f"not found in the mappings: {rule2file}"
        )
        return False

    play_queue.put(file_to_play)
    return True


class GrafanaAlertHttpServer(HTTPServer):
    """
    Wrapper class to store parameters used by GrafanaAlertHandler.
    """

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        server_address,
        rule2file,
        start_hr,
        end_hr,
        play_queue,
        handler_class=GrafanaAlertHandler,
    ):
        super().__init__(server_address, handler_class)
        self.rule2file = rule2file
        self.start_hr = start_hr
        self.end_hr = end_hr
        self.play_queue = play_queue


def run_server(port, rule2file, start_hr, end_hr, play_queue):
    """
    Start HTTP server, will not return unless interrupted.
    """
    logger = logging.getLogger(__name__)

    server_address = ("localhost", port)
    httpd = GrafanaAlertHttpServer(
        server_address, rule2file, start_hr, end_hr, play_queue
    )
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
        help="Configuration file with mapping from 'ruleName' key value "
        "in the Grafana alert payload (exact match) to mp3 file."
        "These should be in the 'rule2mp3' section.",
        default="config.ini",
    )

    return parser.parse_args()


def load_mp3_config(config, config_file):
    """
    Load .ini configuration file. Will exit the program on error.
    :return: dictionary with rule name to mp3 file mappings
    """

    logger = logging.getLogger(__name__)

    mp3config_section_name = "rule2mp3"
    if mp3config_section_name not in config.sections():
        logger.error(
            f"Config file {config_file} does not include "
            f"the {mp3config_section_name} section"
        )
        sys.exit(1)

    # Check that all mp3 files in the configuration are readable.
    mp3suffix = ".mp3"
    for _, file in config[mp3config_section_name].items():
        if not file.endswith(mp3suffix):
            logger.error(f"File {file} does not end with {mp3suffix}")
            sys.exit(1)

        try:
            with open(file, "r", encoding="utf-8"):
                pass
        except IOError as exc:
            logger.error(f"File '{file}' cannot be opened for reading: {exc}")
            sys.exit(1)

    logger.debug(f"File mappings: {config[mp3config_section_name]}")

    return config[mp3config_section_name]


def load_hr_config(config):
    """
    Load start and end hour from configuration file or return defaults.
    :param config:
    :return: tuple of start and end hour
    """

    logger = logging.getLogger(__name__)

    start_hr = 8
    end_hr = 23

    section_name = "start_end"
    if section_name in config.sections():
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

    config = configparser.ConfigParser()
    config_files = config.read(args.config)
    if args.config not in config_files:
        logger.error(f"Failed to load configuration file '{args.config}'")
        sys.exit(1)
    rule2file = load_mp3_config(config, args.config)
    start_hr, end_hr = load_hr_config(config)
    logger.debug(f"Using range: [{start_hr}, {end_hr}] hours")

    play_queue = queue.Queue()

    threading.Thread(
        target=play_mp3, args=(play_queue, args.timeout, args.mpg123), daemon=True
    ).start()

    run_server(server_port, rule2file, start_hr, end_hr, play_queue)


if __name__ == "__main__":
    main()
