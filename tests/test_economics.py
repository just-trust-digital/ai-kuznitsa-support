import unittest
from decimal import Decimal

from backend.economics import compare


class CostTests(unittest.TestCase):
    def test_unknown_inputs_are_not_zero(self):
        result = compare(dict(currency="RUB", dialogues_per_month=83))
        self.assertIn("usage", result["hybrid"]["missing"])
        self.assertIsNone(result["breakeven_dialogues_per_month"])

    def test_point_and_request_unit(self):
        measured = dict(measured_dialogues=10, measured_requests=20,
                        operator_minutes_per_dialogue=0, operator_hourly=0)
        local = dict(**measured, fixed_month=500, local_compute_per_dialogue=20, usage={})
        hybrid = dict(**measured, fixed_month=1000, local_compute_per_dialogue=10,
                      usage=dict(cloud=dict(calls=10, metered_calls=10, input_tokens=1_000_000, output_tokens=0)),
                      rates=dict(cloud=dict(input_per_million=50)))
        report = compare(dict(currency="RUB", dialogues_per_month=100, local=local, hybrid=hybrid))
        self.assertEqual(report["hybrid"]["variable_per_dialogue"], Decimal(15))
        self.assertEqual(report["hybrid"]["variable_per_request"], Decimal("7.5"))
        self.assertEqual(report["hybrid"]["monthly"], Decimal(2500))
        self.assertEqual(report["hybrid"]["per_request_at_volume"], Decimal("12.5"))
        self.assertEqual(report["breakeven_dialogues_per_month"], Decimal(100))
        hybrid["usage"]["cloud"]["calls"] += 1
        report = compare(dict(currency="RUB", dialogues_per_month=100, local=local, hybrid=hybrid))
        self.assertIn("cloud.metering", report["hybrid"]["missing"])
        self.assertIsNone(report["breakeven_dialogues_per_month"])
