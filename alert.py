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
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pprint import pformat
from shutil import which
from subprocess import TimeoutExpired

from logutil import LogLevelAction

play_queue = queue.Queue()


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
        if now.hour < 8 or now.hour > 23:
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
                payload, self.server.rule_name_match, self.server.file_to_play
            )
        except GrafanaPayloadException:
            self._set_response(400)
            return

        self._set_response(200)
        self.wfile.write(f"POST request for {self.path}".encode("utf-8"))


def play_mp3(timeout=30, mpg123="mpg123"):
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
    pass


def handle_grafana_alert(payload, rule_name_to_match, file_to_play):
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

    if rule_name_to_match != rule_name:
        logger.error(
            f"'ruleName' value '{rule_name}' in the payload "
            f"does not contain '{rule_name_to_match}': {payload}"
        )
        return False

    play_queue.put(file_to_play)
    return True


class GrafanaAlertHttpServer(HTTPServer):
    """
    Wrapper class to store parameters used by GrafanaAlertHandler.
    """

    def __init__(
        self,
        server_address,
        rule_name_match,
        file_to_play,
        handler_class=GrafanaAlertHandler,
    ):
        super().__init__(server_address, handler_class)
        self.rule_name_match = rule_name_match
        self.file_to_play = file_to_play


def run_server(port, rule_name_match, file_to_play):
    """
    Start HTTP server, will not return unless interrupted.
    """
    logger = logging.getLogger(__name__)

    server_address = ("localhost", port)
    httpd = GrafanaAlertHttpServer(server_address, rule_name_match, file_to_play)
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
        "--ruleNameMatch",
        help="Value to match the 'ruleName' key value in the payload (exact match)",
        default="CO2 alert",
    )

    return parser.parse_args()


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

    # Search base directory of the program for files to play.
    dir_to_search = os.path.dirname(os.path.realpath(__file__))
    suffix = ".mp3"
    file_list = [
        f
        for f in os.listdir(dir_to_search)
        if os.path.isfile(os.path.join(dir_to_search, f)) and f.endswith(suffix)
    ]
    if len(file_list) == 0:
        logger.error(f"Cannot find a file with {suffix} in {dir_to_search}")
        sys.exit(1)

    file_to_play = os.path.join(dir_to_search, file_list[0])
    logger.info(f"Selected file to play: '{file_to_play}'")

    threading.Thread(
        target=play_mp3, args=(args.timeout, args.mpg123), daemon=True
    ).start()

    run_server(server_port, args.ruleNameMatch, file_to_play)


if __name__ == "__main__":
    main()
