"""
Test alert.py
"""

import queue

import pytest

from alert import GrafanaPayloadException, handle_grafana_alert


def test_payload_no_rule_name_match():
    """
    Payload with not matching 'ruleName' should not play the song.
    """
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "alerting"}
    assert not handle_grafana_alert(
        payload, {rule_name + "bar": "foo.mp3"}, queue.Queue()
    )


def test_payload_no_state_match():
    """
    Payload with 'state' not 'alerting' should not play the song.
    """
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "pending"}
    assert not handle_grafana_alert(payload, {rule_name: "foo.mp3"}, queue.Queue())


def test_payload_lower_case():
    """
    Simple test for case insensitive ruleName matching.
    """
    rule_name = "foo"
    payload = {"ruleName": rule_name.upper(), "state": "alerting"}
    assert handle_grafana_alert(payload, {rule_name: "foo.mp3"}, queue.Queue())


def test_payload_will_play():
    """
    Simple test for song successfully enqueued.
    """
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "alerting"}
    assert handle_grafana_alert(payload, {rule_name: "foo.mp3"}, queue.Queue())


def test_payload_with_exception():
    """
    Improperly formed payload should cause GrafanaPayloadException.
    """
    payload = {"foo": "bar"}
    with pytest.raises(GrafanaPayloadException):
        handle_grafana_alert(payload, {"rule_name": "foo.mp3"}, queue.Queue())
