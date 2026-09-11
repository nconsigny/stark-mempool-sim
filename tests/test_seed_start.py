"""Seed offsets select existing experiments, not a new random stream."""

import argparse
import contextlib
import importlib
import io
import json
import sys
import unittest
from unittest import mock

from bench.common import (add_seed_start_argument, seed_range,
                          workload_parameters)


FIXTURES = {
    "overload": ["--tps", "2", "--window", "500", "--drain", "1000"],
    "economy": ["--tps", "2", "--window", "1000", "--drain", "1000"],
    "levers": ["--transactions", "2", "--window", "1000", "--drain", "1000"],
    "throughput": ["--tps", "2", "--window", "1000", "--drain", "1000"],
    "mainnet": ["--tps", "2", "--privacy-share", "0", "--slots", "1",
                "--slot-length", "1000", "--drain-slots", "1"],
    "placement": ["--entry-subset", "2", "--slots", "2", "--settle-slots", "1",
                  "--latencies", "1"],
    "sweep": ["--axis", "fold_plan", "--values", "wide,binary",
              "--transactions", "2", "--ticks", "2", "--slots", "1"],
    "propagation": ["--objects", "2", "--spread", "100", "--window", "1000"],
    "triage": ["--tps", "2", "--window", "1000", "--drain", "1000"],
}


def output(name, *extra):
    module = importlib.import_module("bench." + name)
    argv = [name, "--nodes", "8", "--degree", "2", "--seeds", "1"]
    argv += FIXTURES[name] + list(extra)
    stream = io.StringIO()
    with mock.patch.object(sys, "argv", argv):
        with contextlib.redirect_stdout(stream):
            module.main()
    return stream.getvalue()


class TestSeedStart(unittest.TestCase):
    def test_the_start_is_always_recorded_and_defaults_to_zero(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--seeds", type=int, default=2)
        add_seed_start_argument(parser)
        args = parser.parse_args([])
        self.assertEqual(list(seed_range(args)), [0, 1])
        self.assertEqual(workload_parameters(args), {"seed_start": 0})
        args = parser.parse_args(["--seed-start", "0"])
        self.assertEqual(workload_parameters(args), {"seed_start": 0})
        args = parser.parse_args(["--seed-start", "100", "--seeds", "32"])
        self.assertEqual(list(seed_range(args)), list(range(100, 132)))

    def test_every_bench_selects_the_same_seeds_five_and_six(self):
        for name in FIXTURES:
            with self.subTest(bench=name):
                full = [json.loads(line) for line in
                        output(name, "--json", "--seeds", "7").splitlines()]
                expected = [row for row in full if row["seed"] in (5, 6)]
                shifted = output(name, "--json", "--seed-start", "5",
                                 "--seeds", "2")
                self.assertEqual(shifted, output(
                    name, "--json", "--seed-start", "5", "--seeds", "2"))
                actual = [json.loads(line) for line in shifted.splitlines()]
                self.assertEqual({row["seed"] for row in actual}, {5, 6})
                for row in full:
                    self.assertEqual(row["workload"].pop("seed_start"), 0)
                for row in actual:
                    self.assertEqual(row["workload"].pop("seed_start"), 5)
                    self.assertNotIn("seed_start", row["variant"])
                    self.assertEqual(row["variant"]["seed"], str(row["seed"]))
                self.assertEqual(actual, expected)

    def test_sweep_csv_rows_are_byte_identical_to_the_absolute_seed_slice(self):
        full = output("sweep", "--seeds", "7").splitlines(keepends=True)
        header = full[0].strip().split(",")
        seed_column = header.index("seed")
        expected = [full[0]]
        for row in full[1:]:
            if int(row.strip().split(",")[seed_column]) in (5, 6):
                expected.append(row)
        self.assertEqual(output("sweep", "--seed-start", "5", "--seeds", "2"),
                         "".join(expected))

    def test_default_text_and_csv_match_explicit_zero(self):
        for name in FIXTURES:
            with self.subTest(bench=name):
                self.assertEqual(output(name), output(name, "--seed-start", "0"))

    def test_every_bench_rejects_a_negative_start(self):
        for name in FIXTURES:
            with self.subTest(bench=name):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        output(name, "--seed-start", "-1")
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
