from alert import handle_grafana_alert


def test_payload_no_rule_name_match():
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "alerting"}
    assert not handle_grafana_alert(payload, rule_name + "bar", "foo.mp3")


def test_payload_no_state_match():
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "pending"}
    assert not handle_grafana_alert(payload, rule_name, "foo.mp3")


def test_payload_will_play():
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "alerting"}
    assert handle_grafana_alert(payload, rule_name, "foo.mp3")
