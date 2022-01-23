from alert import handle_grafana_alert


def test_payload():
    rule_name = "foo"
    payload = {"ruleName": rule_name, "state": "alerting"}
    assert handle_grafana_alert(payload, rule_name + "bar", "foo.mp3")
