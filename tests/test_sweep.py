"""Unclocked cells have explicit windows and cannot silently skip work."""

import contextlib
import io
import json
import math
import sys
import unittest
from unittest import mock

from bench import sweep
from bench.common import random_regular_overlay, schedule_horizon
from starksim.costs import CostTable
from starksim.netsim import CLOCKED_TRIGGERS, NetworkSim, csv_row
from starksim.network import bytes_per_ms

from tests.support import two_node_overlay


def output(*arguments):
    stream = io.StringIO()
    argv = ["sweep", "--nodes", "8", "--degree", "2", "--seeds", "1",
            "--latency", "1", "--transactions", "16"] + list(arguments)
    with mock.patch.object(sys, "argv", argv):
        with contextlib.redirect_stdout(stream):
            sweep.main()
    return stream.getvalue()


WINDOWS = ["--birth-horizon-ms", "1000", "--observation-horizon-ms", "3000"]


class TestSweepHorizon(unittest.TestCase):
    def test_aligned_and_ready_both_prove_without_scheduling_ready_ticks(self):
        scheduled = []
        observed = []
        original_ticks = NetworkSim.schedule_ticks
        original_run = NetworkSim.run

        def ticks(sim, count, *args, **kwargs):
            self.assertIn(sim.aggregation_trigger, CLOCKED_TRIGGERS)
            scheduled.append((sim.aggregation_trigger, count))
            return original_ticks(sim, count, *args, **kwargs)

        def run(sim, until=None):
            self.assertIsNotNone(until)
            self.assertTrue(math.isfinite(until))
            observed.append(until)
            return original_run(sim, until=until)

        args = ["--axis", "aggregation_trigger", "--values", "aligned,when_ready",
                "--set", "proving_budget=500", "--json"] + WINDOWS
        with mock.patch.object(NetworkSim, "schedule_ticks", ticks):
            with mock.patch.object(NetworkSim, "run", run):
                rows = [json.loads(line) for line in output(*args).splitlines()]
        self.assertEqual(scheduled, [("aligned", 6)])
        self.assertEqual(observed, [3000, 3000])
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertGreater(row["metrics"]["proofs_completed"], 0)
            record = row["workload"]["schedule"]
            self.assertEqual(record["proving_budget_ms"], 500)
            self.assertEqual(record["proving_budget_source"], "explicit")
            self.assertEqual(record["birth_horizon_ms"], 1000)
            self.assertEqual(record["observed_until_ms"], 3000)
            self.assertEqual(record["slot_count"], 1)
            self.assertNotIn("schedule", row["variant"])
        self.assertEqual(rows[1]["workload"]["schedule"]["tick_count"], 0)
        self.assertEqual(rows[1]["workload"]["schedule"]["mode"], "unclocked")
        self.assertEqual(rows[0]["workload"]["schedule"]["mode"], "clocked")
        births = []
        for row in rows:
            births.append([item["born_ms"] for item in
                           row["metrics"]["transaction_outcomes"]])
        self.assertEqual(births[0], births[1])
        self.assertEqual(output(*args), output(*args))

    def test_common_helper_skips_unclocked_ticks(self):
        sim = NetworkSim(two_node_overlay(), aggregation_trigger="when_ready",
                         proving_budget=500, slot_length=100)
        with mock.patch.object(NetworkSim, "schedule_ticks",
                               side_effect=AssertionError("unclocked ticks")):
            self.assertEqual(schedule_horizon(sim, 250),
                             {"tick_count": 0, "slot_count": 2})
        sim.run(until=250)
        self.assertEqual(sim.slots_run, 2)

    def test_common_helper_preserves_clocked_period_counts(self):
        sim = NetworkSim(two_node_overlay(), aggregation_interval=100,
                         slot_length=200)
        self.assertEqual(schedule_horizon(sim, 450),
                         {"tick_count": 4, "slot_count": 2})
        self.assertEqual(len(sim.engine.calendar), 2 * 4 + 2)
        for invalid in (-1, float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "horizon"):
                schedule_horizon(sim, invalid)

    def test_legacy_csv_and_endpoint_equal_the_original_scheduler(self):
        for trigger in ("aligned", "spread"):
            for shape in ("wide", "binary"):
                settings = dict(nodes=8, degree=2, latency=1, seed=3,
                                transactions=8, ticks=3, slots=1,
                                uplink_mbps=50, prove_cost=30,
                                fold_plan=shape, aggregation_trigger=trigger)
                schedule = {}
                actual = sweep.one_run(**settings, schedule_record=schedule)
                old = NetworkSim(
                    random_regular_overlay(8, 2, 1, 3), seed=3,
                    capacity=bytes_per_ms(50), aggregation_interval=500,
                    slot_length=2000, proposer_id=0, block_budget=5,
                    costs=CostTable(verify_recv=2,
                                    prove_recursive=lambda deps: 30, seed=3),
                    fold_plan=shape, aggregation_trigger=trigger)
                for index in range(8):
                    old.schedule_birth(index * (2000 / 8), "q",
                                       fee=float(index % 5))
                old.schedule_ticks(3)
                old.schedule_slots(1)
                old.run()
                old.workload_generator_version = "legacy:sweep:v1"
                self.assertEqual(csv_row(actual.metrics()), csv_row(old.metrics()))
                self.assertEqual(actual.metrics(), old.metrics())
                self.assertEqual(schedule["observation_horizon_source"],
                                 "calendar_drain")
                self.assertEqual(schedule["tick_count_source"], "ticks")
                self.assertEqual(schedule["proving_budget_source"],
                                 "aggregation_interval")

    def test_invalid_cells_are_refused_with_cli_errors(self):
        cases = [
            (["--axis", "aggregation_trigger", "--values", "when_ready"],
             "horizon"),
            (["--axis", "aggregation_trigger", "--values", "when_ready"]
             + WINDOWS, "proving_budget"),
            (["--axis", "aggregation_trigger", "--values", "when_ready",
              "--set", "proving_budget=500", "--set", "aggregation_interval=1000"]
             + WINDOWS, "cannot affect"),
            (["--axis", "aggregation_trigger", "--values", "when_ready",
              "--set", "proving_budget=500", "--set", "on_tick_overrun=fifo"]
             + WINDOWS, "cannot affect"),
            (["--axis", "fold_plan", "--values", "wide", "--ticks", "1"]
             + WINDOWS, "replace"),
            (["--axis", "fold_plan", "--values", "wide",
              "--birth-horizon-ms", "1000"], "together"),
            (["--axis", "fold_plan", "--values", "wide",
              "--birth-horizon-ms", "2000", "--observation-horizon-ms", "1000"],
             "cover"),
            (["--axis", "fold_plan", "--values", "wide",
              "--birth-horizon-ms", "nan", "--observation-horizon-ms", "1000"],
             "finite"),
            (["--axis", "proving_budget", "--values", "inf"] + WINDOWS,
             "finite"),
            (["--axis", "request_timeout", "--values", "10",
              "--set", "relay_policy=announce_fetch"], "retries"),
            (["--axis", "size_tx_body", "--values", "-1"] + WINDOWS,
             "nonnegative"),
            (["--axis", "cpu_queue_jobs", "--values", "bad"] + WINDOWS,
             "--values"),
            (["--axis", "pin_own_aggregate", "--values", "maybe"] + WINDOWS,
             "true or false"),
            (["--axis", "fold_plan", "--values", "wide", "--prove-cost", "nan"],
             "finite"),
            (["--axis", "fold_plan", "--values", "wide", "--seeds", "0"],
             "positive"),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    with self.assertRaises(SystemExit) as raised:
                        output(*args, "--json")
                self.assertEqual(raised.exception.code, 2)
                self.assertIn(message, error.getvalue())


class TestSweepAxes(unittest.TestCase):
    def test_participation_axes_reach_the_variant_and_repeat_exactly(self):
        for name, value in (("aggregator_share", "0.5"),
                             ("aggregator_placement", "central"),
                             ("relay_forwarding", "all"),
                             ("proposer_role", "relay")):
            with self.subTest(axis=name):
                arguments = ["--axis", name, "--values", value, "--json"]
                if name != "aggregator_share":
                    arguments += ["--set", "aggregator_share=0.5"]
                text = output(*arguments, *WINDOWS)
                self.assertEqual(text, output(*arguments, *WINDOWS))
                row = json.loads(text)
                expected = 0.5 if name == "aggregator_share" else value
                self.assertEqual(row["variant"][name], expected)
                self.assertEqual(len(row["variant"]["relay_node_ids"]), 4)

    def test_new_scalar_axes_reach_the_variant_with_their_types(self):
        for name, value in (
                ("egress_queue_bytes", "1000"), ("cpu_queue_jobs", "1"),
                ("seen_limit", "2"), ("candidate_limit", "2"),
                ("coverage_cache_limit", "2"), ("capacity", "100"),
                ("link_capacity", "200"), ("proposer_id", "1")):
            with self.subTest(axis=name):
                row = json.loads(output("--axis", name, "--values", value,
                                        "--json", *WINDOWS))
                self.assertEqual(row["variant"][name], float(value))
                self.assertIsInstance(row["workload"]["axis_value"], (int, float))
        row = json.loads(output("--axis", "pin_own_aggregate", "--values", "false",
                                "--set", "candidate_limit=2", "--json", *WINDOWS))
        self.assertIs(row["variant"]["pin_own_aggregate"], False)
        row = json.loads(output("--axis", "request_timeout", "--values", "100",
                                "--set", "relay_policy=announce_fetch",
                                "--json", *WINDOWS))
        self.assertEqual(row["variant"]["request_timeout"], 100)

    def test_size_axes_construct_the_size_model(self):
        for name in sweep.AXES:
            if not name.startswith("size_"):
                continue
            with self.subTest(axis=name):
                row = json.loads(output("--axis", name, "--values", "17",
                                        "--json", *WINDOWS))
                self.assertEqual(row["variant"][name], 17)

    def test_new_optional_axes_accept_none_existing_conversions_stay(self):
        for name in ("egress_queue_bytes", "cpu_queue_jobs", "seen_limit",
                     "candidate_limit", "coverage_cache_limit", "capacity",
                     "link_capacity", "request_timeout", "fee", "lifetime"):
            self.assertIsNone(sweep.AXES[name]("none"))
        self.assertIs(sweep.AXES["entry_subset"], int)
        self.assertIs(sweep.AXES["proving_budget"], float)
        row = json.loads(output("--axis", "capacity", "--values", "none",
                                "--json", *WINDOWS))
        self.assertIsNone(row["workload"]["axis_value"])
        self.assertEqual(row["variant"]["capacity"], -1)

    def test_workload_axes_reach_births_not_constructor_settings(self):
        captured = []
        original = NetworkSim.schedule_birth

        def schedule(sim, t, kind, node_id=None, fee=0, lifetime=None):
            captured.append((t, kind, fee, lifetime))
            return original(sim, t, kind, node_id=node_id, fee=fee, lifetime=lifetime)

        with mock.patch.object(NetworkSim, "schedule_birth", schedule):
            rows = [json.loads(line) for line in output(
                "--axis", "transactions", "--values", "2,4", "--set", "kind=p",
                "--set", "fee=9", "--set", "lifetime=50", "--json",
                *WINDOWS).splitlines()]
        self.assertEqual([item[0] for item in captured], [0, 500, 0, 250, 500, 750])
        self.assertEqual([item[1:] for item in captured], [("p", 9, 50)] * 6)
        for row, count in zip(rows, (2, 4)):
            self.assertEqual(row["metrics"]["arrived_txs"], count)
            self.assertEqual(row["workload"]["transactions"], count)
            self.assertEqual(row["workload"]["schedule"]["transactions"], count)
            self.assertNotIn("transactions", row["variant"])
            self.assertEqual(row["metrics"]["expired_txs"], count)


if __name__ == "__main__":
    unittest.main()
