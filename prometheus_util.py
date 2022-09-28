import logging
from prometheus_api_client import (
    MetricsList,
    PrometheusApiClientException,
)


def extract_metric_from_data(data):
    """
    Given JSON string received as a result of Prometheus query,
    extract metric value and return as string
    :param data: JSON string
    :return: metric value as string
    """
    temp_list = MetricsList(data)
    metric = temp_list[0]
    return str(metric.metric_values.y[0])


def acquire_prometheus_temperature(prometheus_connect, sensor_name):
    """
    Return single temperature reading from Prometheus query.
    :param prometheus_connect: Prometheus connect instance
    :param sensor_name: name of the temperature sensor
    :return: temperature as float value in degrees of Celsius
    """

    logger = logging.getLogger(__name__)

    temp_value = None

    try:
        temp_data = prometheus_connect.custom_query(
            "last_over_time(temperature{sensor='" + sensor_name + "'}[30m])"
        )
        logger.debug(f"Got Prometheus reply for sensor '{sensor_name}': {temp_data}")
        temp = extract_metric_from_data(temp_data)
        temp_value = float(temp)
    except (PrometheusApiClientException, IndexError) as req_exc:
        logger.error(
            f"cannot get data for temperature sensor '{sensor_name}' from Prometheus: {req_exc}"
        )

    return temp_value
