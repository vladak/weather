#!/usr/bin/env python3
"""

Receive alerts from Grafana and play a mp3 file if the alert
matches a condition.

"""

import os
import subprocess
import logging
import json
import threading
import sys
from pprint import pprint
from datetime import datetime
from subprocess import TimeoutExpired
from glob import glob

from http.server import BaseHTTPRequestHandler, HTTPServer


file_to_play = None

# TODO: figure out a way how to parametrize the class with file/time/timeout
#       (subclassing ?)
class SrvClass(BaseHTTPRequestHandler):
    """
    This class is meant to handle POST requests from Grafana,
    specifically requests to alert.
    """
    def _set_response(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_POST(self):
        """
        Handle POST request. In theory requests not matching the expected
        criteria should return "bad request" or such however to keep Grafana
        happy it always returns success.
        """
        logger = logging.getLogger(__name__)

        if not self.headers.get('User-Agent') == "Grafana":
            logger.info("Not a Grafana POST request, ignoring")
            # TODO send response
            return

        now = datetime.now()
        if now.hour < 8 or now.hour > 23:
            logger.info("Request received outside of open time window, ignoring")
            # TODO send response
            return

        content_length = int(self.headers['Content-Length'])
        if content_length == 0:
            logger.info("Empty content, ignoring")
            # TODO send response
            return

        post_data = self.rfile.read(content_length)
        if post_data is None:
            logger.info("Empty data, ignoring")
            # TODO send response
            return

        data_utf8 = post_data.decode('utf-8')
        payload = json.loads(data_utf8)
        # TODO print using logger.debug()
        pprint(payload)

        # TODO: filter based on payload

        self._set_response()
        self.wfile.write("POST request for {}".format(self.path).encode('utf-8'))

        try:
            handle_alert()
        except OSError as e:
            logger.error(f"Got exception while trying to play {file_to_play}: {e}")


def play_mp3(path, timeout=30):
    """
    Play given file via mpg123.
    """
    # TODO: ideally, this should enqueue the request to play to handle multiple
    #       alerts happening around the same time

    logger = logging.getLogger(__name__)

    if not os.path.exists(path):
        raise OSError(f"file '{path}' does not exist")

    logger.info(f"Playing {path}")
    proc = subprocess.Popen(['mpg123', '-q', path])
    try:
        outs, errs = proc.communicate(timeout=timeout)
    except TimeoutExpired:
        proc.terminate()
        outs, errs = proc.communicate()


def handle_alert():
    thread = threading.Thread(target=play_mp3, args=(file_to_play,), daemon=True)
    thread.start()


def run_server(port, server_class=HTTPServer, handler_class=SrvClass):
    """
    Start HTTP server, will not return unless interrupted.
    """
    logger = logging.getLogger(__name__)

    server_address = ('localhost', port)
    httpd = server_class(server_address, handler_class)
    logger.info('Starting HTTP server...')

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    httpd.server_close()
    logger.info('Stopping HTTP server...')


if __name__ == "__main__":
    # TODO: argparse
    server_port=8333

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # TODO: check that mpg123 is installed and in PATH

    # Search base directory of the program for files to play.
    dir_to_search = os.path.dirname(os.path.realpath(__file__))
    suffix = ".mp3"
    file_list = [f for f in os.listdir(dir_to_search)
                 if os.path.isfile(os.path.join(dir_to_search, f)) and f.endswith(suffix)]
    if len(file_list) == 0:
        logger.error(f"Cannot find a file with {suffix} in {dir_to_search}")
        sys.exit(1)

    file_to_play = os.path.join(dir_to_search, file_list[0])
    logger.info(f"Selected file to play: '{file_to_play}'")

    run_server(server_port)
