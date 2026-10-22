import unittest
from unittest.mock import Mock

from grafana_hs_mcp.grafana_client import GrafanaClient


class GrafanaClientTest(unittest.TestCase):
    def setUp(self):
        self.client = GrafanaClient.__new__(GrafanaClient)
        self.client.config = Mock()
        self.client.auth = Mock()
        self.client.session = Mock()
        self.client.request = Mock()

    def test_query_prometheus_uses_range_endpoint_and_returns_data(self):
        response = Mock()
        response.json.return_value = {"status": "success", "data": {"result": []}}
        self.client.request.return_value = response

        result = self.client.query_prometheus(
            "sum(rate(container_cpu_usage_seconds_total[5m]))",
            start="now-1h",
            end="now",
            step="30s",
            datasource_uid="victoriametrics",
        )

        self.assertEqual(result["status"], "success")
        args, kwargs = self.client.request.call_args
        self.assertEqual(
            args,
            (
                "GET",
                "/api/datasources/uid/victoriametrics/resources/api/v1/query_range",
            ),
        )
        self.assertEqual(
            kwargs["params"]["query"],
            "sum(rate(container_cpu_usage_seconds_total[5m]))",
        )
        self.assertEqual(kwargs["params"]["step"], "30s")

    def test_update_dashboard_requires_uid(self):
        with self.assertRaises(ValueError):
            self.client.update_dashboard({"title": "UCS CI"})

    def test_dashboard_write_requires_title(self):
        with self.assertRaises(ValueError):
            self.client.create_dashboard({})


if __name__ == "__main__":
    unittest.main()
