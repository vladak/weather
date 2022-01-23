from alert import handle_grafana_alert, RULE_NAME_MATCH

RULE_NAME_MATCH = 'bar'


def test_payload():
    payload = {'ruleName': 'foo', 'state': 'alerting'}
    assert not handle_grafana_alert(payload)
