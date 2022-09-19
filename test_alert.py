"""
Test musicalert.py
"""

import json
import queue

import pytest

from musicalert import GrafanaPayloadException, handle_grafana_payload


def test_payload_no_alert_name_match():
    """
    Payload with not matching 'alertname' should not play the song.
    """
    alert_name = "foo"
    payload = {"alerts": [{"labels": {"alertname": alert_name}}], "status": "alerting"}
    assert not handle_grafana_payload(
        payload, {alert_name + "bar": "foo.mp3"}, queue.Queue()
    )


def test_payload_no_state_match():
    """
    Payload with 'state' not 'alerting' should not play the song.
    """
    alert_name = "foo"
    payload = {"alerts": [{"labels": {"alertname": alert_name}}], "status": "pending"}
    assert not handle_grafana_payload(payload, {alert_name: "foo.mp3"}, queue.Queue())


def test_payload_lower_case():
    """
    Simple test for case insensitive alertname matching.
    """
    alert_name = "foo"
    payload = {
        "alerts": [{"labels": {"alertname": alert_name.upper()}}],
        "status": "alerting",
    }
    assert handle_grafana_payload(payload, {alert_name: "foo.mp3"}, queue.Queue())


def test_payload_will_play():
    """
    Simple test for file successfully enqueued.
    """
    alert_name = "foo"
    payload = {"alerts": [{"labels": {"alertname": alert_name}}], "status": "alerting"}
    play_queue = queue.Queue()
    file_mp3 = "foo.mp3"
    assert handle_grafana_payload(payload, {alert_name: file_mp3}, play_queue)
    assert play_queue.get() == file_mp3


def test_payload_with_exception():
    """
    Improperly formed payload should cause GrafanaPayloadException.
    """
    payload = {"foo": "bar"}
    with pytest.raises(GrafanaPayloadException):
        handle_grafana_payload(payload, {"alert_name": "foo.mp3"}, queue.Queue())


def test_grafana_payload():
    """
    This is simple smoke test whether handle_grafana_payload() can actually
    digest sample Grafana alerting payload.
    """
    with open("alerting_payload.json", "r", encoding="utf-8") as file_obj:
        data = file_obj.read()
        payload = json.loads(data)

    handle_grafana_payload(payload, {"alert_name": "foo.mp3"}, queue.Queue())
