"""
Test musicalert.py
"""
import datetime
import json
import queue
import tempfile
from unittest.mock import Mock

import pytest
import tomli

from musicalert import (
    GrafanaAlertHandler,
    GrafanaPayloadException,
    handle_grafana_payload,
    load_mp3_config,
)


def test_payload_no_alert_name_match():
    """
    Payload with not matching 'alertname' should not play the song.
    """
    alert_name = "foo"
    payload = {
        "alerts": [{"labels": {"alertname": alert_name}, "status": "firing"}],
        "status": "firing",
    }
    assert not handle_grafana_payload(
        payload, {"foo.mp3": alert_name + "bar"}, queue.Queue()
    )


def test_payload_no_state_match():
    """
    Payload with 'state' not 'firing' should not play the song.
    """
    alert_name = "foo"
    payload = {
        "alerts": [{"labels": {"alertname": alert_name}, "status": "resolved"}],
        "status": "firing",
    }
    assert not handle_grafana_payload(payload, {"foo.mp3": alert_name}, queue.Queue())


def test_payload_lower_case():
    """
    Simple test for case insensitive alertname matching.
    """
    alert_name = "foo"
    payload = {
        "alerts": [{"labels": {"alertname": alert_name.upper()}, "status": "firing"}],
        "status": "firing",
    }
    assert not handle_grafana_payload(payload, {"foo.mp3": alert_name}, queue.Queue())


def test_payload_will_play_alert_name():
    """
    Simple test for file successfully enqueued based on alert name.
    """
    alert_name = "foo"
    payload = {
        "alerts": [{"labels": {"alertname": alert_name}, "status": "firing"}],
        "status": "firing",
    }
    play_queue = queue.Queue()
    file_mp3 = "foo.mp3"
    assert handle_grafana_payload(payload, {file_mp3: alert_name}, play_queue)
    assert play_queue.get() == file_mp3


def test_payload_will_play_value():
    """
    Simple test for file successfully enqueued based on both alert name and payload value.
    """
    alert_name = "foo"
    payload = {
        "alerts": [
            {
                "labels": {"alertname": alert_name},
                "status": "firing",
                "valueString": "bar",
            }
        ],
        "status": "firing",
    }
    play_queue = queue.Queue()
    file_mp3 = "foo.mp3"
    assert handle_grafana_payload(payload, {file_mp3: [alert_name, "bar"]}, play_queue)
    assert play_queue.get() == file_mp3


def test_payload_will_play_value_regexp():
    """
    Simple test for file successfully enqueued based on both alert name and payload value.
    """
    alert_name = "foo"
    payload = {
        "alerts": [
            {
                "labels": {"alertname": alert_name},
                "status": "firing",
                "valueString": "one bar two",
            }
        ],
        "status": "firing",
    }
    play_queue = queue.Queue()
    file_mp3 = "foo.mp3"
    assert handle_grafana_payload(
        payload, {file_mp3: [alert_name, ".*bar.*"]}, play_queue
    )
    assert play_queue.get() == file_mp3


def test_payload_will_play_value_negative():
    """
    Simple test for file successfully enqueued based on both alert name and payload value.
    """
    alert_name = "foo"
    payload = {
        "alerts": [
            {
                "labels": {"alertname": alert_name},
                "status": "firing",
                "valueString": "huh",
            }
        ],
        "status": "firing",
    }
    play_queue = queue.Queue()
    file_mp3 = "foo.mp3"
    assert not handle_grafana_payload(
        payload, {file_mp3: [alert_name, "bar"]}, play_queue
    )
    assert play_queue.empty()


def test_payload_with_exception():
    """
    Improperly formed payload should cause GrafanaPayloadException.
    """
    payload = {"foo": "bar"}
    with pytest.raises(GrafanaPayloadException):
        handle_grafana_payload(payload, {"foo.mp3": "alert_name"}, queue.Queue())


def test_grafana_payload():
    """
    This is simple smoke test whether handle_grafana_payload() can actually
    digest sample Grafana alerting payload.
    """
    with open("alerting_payload.json", "r", encoding="utf-8") as file_obj:
        data = file_obj.read()
        payload = json.loads(data)

    handle_grafana_payload(payload, {"foo.mp3": "alert_name"}, queue.Queue())


def test_time_check():
    """
    Test the "do not disturb" mechanism of GrafanaAlertHandler.
    """

    class TestableGrafanaAlertHandler(GrafanaAlertHandler):
        """
        Normally on init GrafanaAlertHandler calls self.setup() that requires
        some operations on the request that the Mock object cannot provide,
        like len() on sub-mock objects. So, short-circuit them here.
        """

        def handle(self):
            pass

        def finish(self) -> None:
            pass

    mock_server = Mock()
    # Use non-defaults.
    mock_server.start_hr = 9
    mock_server.end_hr = 22
    response_stub = '{"foo": "bar"}'
    status_code = 200
    mock_request = Mock(
        **{
            "json.return_value": json.loads(response_stub),
            "text.return_value": response_stub,
            "status_code": status_code,
            "ok": status_code == 200,
        }
    )
    handler = TestableGrafanaAlertHandler(
        client_address=tuple("127.0.0.1, 8888"),
        server=mock_server,
        request=mock_request,
    )

    # positive test 1
    now = datetime.datetime(year=2023, month=8, day=25, hour=16, minute=44)
    assert not handler.do_not_disturb(now)

    # positive test 2
    now = datetime.datetime(year=2023, month=8, day=25, hour=9, minute=1)
    assert not handler.do_not_disturb(now)

    # negative test 1
    now = datetime.datetime(year=2023, month=8, day=25, hour=22, minute=5)
    assert handler.do_not_disturb(now)

    # negative test 2
    now = datetime.datetime(year=2023, month=8, day=25, hour=8, minute=44)
    assert handler.do_not_disturb(now)


def test_config_load():
    """
    Test that config file loads successfully with expected content.
    """
    config = """
    [global]
    loglevel = "debug"
    
    [mp3match]
    "{TMP_FILE}" = ["Foo", "[a-z]Bar.*"]
    """
    with tempfile.NamedTemporaryFile(prefix="Foo", suffix=".mp3") as tmp:
        config = config.format(TMP_FILE=tmp.name)
        config = tomli.loads(config)
        mp3match = load_mp3_config(config, "test")
        assert len(dict(mp3match.items())) > 0
