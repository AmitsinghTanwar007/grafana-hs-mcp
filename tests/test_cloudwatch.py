import copy
import unittest
from unittest.mock import Mock, patch

import anyio

from grafana_hs_mcp import server
from grafana_hs_mcp.grafana_client import GrafanaClient


class CloudWatchTest(unittest.TestCase):
    def setUp(self):
        self.client = GrafanaClient.__new__(GrafanaClient)
        self.client.request = Mock()
        self.results = {
            "A": {"frames": [{"data": {"values": [[1], [2]]}}]},
            "B": {"error": "denied"},
        }
        self.client.request.return_value.json.return_value = {"results": self.results}
        self.targets = [
            {
                "refId": "A",
                "region": "us-east-1",
                "namespace": "AWS/WAFV2",
                "metricName": "BlockedRequests",
                "dimensions": {
                    "WebACL": ["hyperswitch-waf"],
                    "Rule": ["ALL"],
                },
                "statistic": "Sum",
                "period": "300",
                "datasource": {"uid": "${cloudwatch_ds}"},
            }
        ]
        self.log_targets = [
            {
                "refId": "A",
                "queryMode": "Logs",
                "region": "eu-west-1",
                "logGroupNames": ["/aws/eks/example/cluster"],
                "queryString": (
                    "fields @timestamp, @message "
                    "| sort @timestamp desc | limit 100"
                ),
            }
        ]

    def test_metric_payload_preserves_target_and_uses_exact_epoch_window(self):
        before = copy.deepcopy(self.targets)

        result = self.client.query_cloudwatch(
            self.targets,
            start="2026-11-06T03:21:44Z",
            end="2026-11-07T03:21:44Z",
        )

        args, kwargs = self.client.request.call_args
        self.assertEqual(args, ("POST", "/api/ds/query"))
        body = kwargs["json"]
        self.assertEqual(int(body["to"]) - int(body["from"]), 86400000)
        self.assertEqual(body["from"], "1793935304000")
        self.assertEqual(body["queries"][0]["dimensions"], self.targets[0]["dimensions"])
        self.assertEqual(
            body["queries"][0]["datasource"],
            {"type": "cloudwatch", "uid": "cloudwatch"},
        )
        self.assertEqual(self.targets, before)
        self.assertEqual(result["results"], self.results)

    def test_empty_invalid_or_duplicate_queries_are_rejected(self):
        for targets in (
            [],
            [None],
            [{"refId": "A"}, {"refId": "A"}],
            [{"refId": ""}],
            [{}] * 101,
        ):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                self.client.query_cloudwatch(targets)
        self.client.request.assert_not_called()

    def test_unsupported_mode_and_unresolved_variables_are_rejected(self):
        for target in (
            {"queryMode": "Log"},
            {"queryMode": []},
            {"region": "$region"},
            {"dimensions": {"WebACL": ["${webacl}"]}},
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.client.query_cloudwatch([target])
        self.client.request.assert_not_called()

    def test_invalid_time_window_rejected(self):
        with self.assertRaises(ValueError):
            self.client.query_cloudwatch(
                self.targets, start="2026-11-07", end="2026-11-06"
            )
        self.client.request.assert_not_called()

    def test_missing_ref_ids_generated_for_expression_queries(self):
        self.client.query_cloudwatch([{"expression": "SUM(METRICS())"}])

        body = self.client.request.call_args.kwargs["json"]
        self.assertEqual(body["queries"][0]["refId"], "Q1")
        self.assertEqual(body["queries"][0]["expression"], "SUM(METRICS())")

    def test_logs_payload_uses_native_target_and_defaults_to_cwli(self):
        before = copy.deepcopy(self.log_targets)

        result = self.client.query_cloudwatch(
            self.log_targets,
            start="2026-11-06T03:21:44Z",
            end="2026-11-07T03:21:44Z",
            datasource_uid="cw-eu",
        )

        body = self.client.request.call_args.kwargs["json"]
        target = body["queries"][0]
        self.assertEqual(target["queryMode"], "Logs")
        self.assertEqual(target["queryLanguage"], "CWLI")
        self.assertEqual(target["logGroupNames"], self.log_targets[0]["logGroupNames"])
        self.assertEqual(target["queryString"], self.log_targets[0]["queryString"])
        self.assertEqual(
            target["datasource"], {"type": "cloudwatch", "uid": "cw-eu"}
        )
        self.assertEqual(target["maxDataPoints"], 1440)
        self.assertEqual(self.log_targets, before)
        self.assertEqual(result["results"], self.results)

    def test_logs_supports_modern_log_groups_and_legacy_expression(self):
        target = {
            "queryMode": "Logs",
            "region": "us-east-1",
            "logGroups": [
                {"name": "/aws/lambda/example"},
                {"arn": "arn:aws:logs:us-east-1:123456789012:log-group:example"},
            ],
            "expression": "stats count() by bin(5m)",
        }

        self.client.query_cloudwatch([target])

        body = self.client.request.call_args.kwargs["json"]
        sent = body["queries"][0]
        self.assertEqual(sent["queryString"], target["expression"])
        self.assertEqual(sent["logGroups"], target["logGroups"])
        self.assertEqual(sent["refId"], "Q1")

    def test_invalid_logs_targets_are_rejected(self):
        valid = self.log_targets[0]
        for target in (
            {"queryMode": "Logs", "region": "eu-west-1", "logGroupNames": ["g"]},
            {"queryMode": "Logs", "queryString": "fields @message", "logGroupNames": ["g"]},
            {"queryMode": "Logs", "region": "eu-west-1", "queryString": "fields @message"},
            {**valid, "logGroupNames": "/aws/eks/example/cluster"},
            {**valid, "logGroupNames": [""]},
            {**valid, "logGroupNames": None, "logGroups": [{}]},
            {**valid, "queryLanguage": "OpenSearch"},
            {**valid, "queryLanguage": []},
            {**valid, "queryString": "fields @message | filter service = $service"},
        ):
            invalid = dict(target)
            with self.subTest(target=invalid), self.assertRaises(ValueError):
                self.client.query_cloudwatch([invalid])
        self.client.request.assert_not_called()

    def test_mcp_wrapper_routes_instance_and_logs_target(self):
        client = Mock()
        client.query_cloudwatch.return_value = {"results": {}}

        async def call():
            return await server.query_cloudwatch(
                self.log_targets,
                datasource_uid="cw-eu",
                grafana_instance="eu-prod",
            )

        with patch.object(server, "get_client", return_value=client) as get_client:
            self.assertEqual(anyio.run(call), {"results": {}})
        get_client.assert_called_once_with("eu-prod")
        call_kwargs = client.query_cloudwatch.call_args.kwargs
        self.assertEqual(call_kwargs["datasource_uid"], "cw-eu")
        self.assertEqual(call_kwargs["queries"], self.log_targets)


if __name__ == "__main__":
    unittest.main()
