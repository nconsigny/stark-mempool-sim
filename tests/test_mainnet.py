#!/usr/bin/env python3
"""The mainnet bench's workload mix and records describe their own run."""

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from bench import mainnet
from starksim.arena import KIND_PQ, KIND_PRIVACY


class TestMixedKinds(unittest.TestCase):
    def test_the_share_rounds_to_the_nearest_count(self):
        # A drawn-per-birth mix would only approach the label in expectation;
        # the realised count must be the rounded share even when the division
        # is not clean, and the record carries it.
        kinds = mainnet.mixed_kinds(20, 0.25, seed=3)
        self.assertEqual(kinds.count(KIND_PRIVACY), 5)
        self.assertEqual(kinds.count(KIND_PQ), 15)
        self.assertEqual(mainnet.mixed_kinds(20, 0.33, seed=1)
                         .count(KIND_PRIVACY), 7)

    def test_the_mix_stream_identity_is_pinned(self):
        # The {seed}:mix stream is an experiment contract (CONTRIBUTING.md):
        # renaming or reordering it would silently re-pair every published
        # comparison, so the exact interleaving is pinned for two seeds.
        self.assertEqual(mainnet.mixed_kinds(6, 0.5, seed=3),
                         ["q", "q", "q", "p", "p", "p"])
        self.assertEqual(mainnet.mixed_kinds(8, 0.5, seed=7),
                         ["p", "p", "q", "q", "q", "q", "p", "p"])

    def test_the_extremes_are_pure(self):
        self.assertEqual(set(mainnet.mixed_kinds(10, 0.0, seed=1)),
                         {KIND_PQ})
        self.assertEqual(set(mainnet.mixed_kinds(10, 1.0, seed=1)),
                         {KIND_PRIVACY})


def bench_stdout(argv):
    stdout = io.StringIO()
    with mock.patch.object(sys, "argv", argv):
        with contextlib.redirect_stdout(stdout):
            mainnet.main()
    return stdout.getvalue()


def bench_records(argv):
    return [json.loads(line) for line in bench_stdout(argv).splitlines()]


SMALL = ["mainnet", "--json", "--nodes", "2", "--degree", "1",
         "--slot-length", "1000", "--slots", "2", "--drain-slots", "1",
         "--seeds", "1", "--presets", "eip_faithful"]


class TestValidation(unittest.TestCase):
    def test_a_rate_that_rounds_to_no_transactions_is_refused(self):
        # round(0.5) is 0 under banker's rounding; the run would print the
        # requested rate over zero births.
        argv = SMALL + ["--tps", "0.5", "--slots", "1"]
        with self.assertRaises(SystemExit):
            bench_records(argv)

    def test_a_share_outside_the_unit_interval_is_refused(self):
        for share in ("1.5", "-0.2"):
            argv = SMALL + ["--tps", "2", "--privacy-share", share]
            with self.assertRaises(SystemExit):
                bench_records(argv)

    def test_a_preset_touching_an_owned_field_is_refused(self):
        # v1_equivalent unbinds capacity and the proving budget, which this
        # bench derives from its own flags: accepting it would print an
        # uplink the run does not use.
        argv = SMALL[:-1] + ["v1_equivalent", "--tps", "2"]
        with self.assertRaises(SystemExit):
            bench_records(argv)

    def test_a_seed_override_is_refused(self):
        # The topology and mix are built from the sweep seed; a preset
        # override reaching NetworkSim's seed would give the record a
        # different identity than the workload it describes.
        argv = SMALL[:-1] + ["eip_faithful+seed=99", "--tps", "2"]
        with self.assertRaises(SystemExit):
            bench_records(argv)


class TestJsonRecords(unittest.TestCase):
    def test_each_record_carries_its_own_axis_values(self):
        # A sweep is one argument string but many runs; each record must say
        # which point it measured, not the sweep strings it came from.
        argv = SMALL + ["--tps", "2", "--privacy-share", "0.0,0.5",
                        "--leanstark-factor", "1,2", "--anchor-ms", "417"]
        records = bench_records(argv)
        self.assertEqual(len(records), 4)
        seen = set()
        for record in records:
            workload = record["workload"]
            self.assertEqual(workload["tps"], 2.0)
            self.assertEqual(workload["transactions"], 4)
            self.assertEqual(workload["anchor_ms"], 417.0)
            self.assertEqual(workload["fee_model"], "cycle5")
            self.assertEqual(workload["privacy_born"],
                             round(4 * workload["privacy_share"]))
            seen.add((workload["privacy_share"],
                      workload["leanstark_factor"]))
        self.assertEqual(seen, {(0.0, 1.0), (0.0, 2.0),
                                (0.5, 1.0), (0.5, 2.0)})

    def test_the_axes_reach_the_simulation_not_only_the_label(self):
        # A privacy transaction carries a 256 kB witness against a PQ
        # transaction's 2.5 kB: if the share axis reaches the run, the pure
        # extremes must offer clearly different bytes. The margin is only 2x
        # because kind-independent wrapper traffic dominates this tiny
        # fixture.
        argv = SMALL + ["--tps", "4"]
        pure_pq = bench_records(argv + ["--privacy-share", "0"])[0]
        pure_privacy = bench_records(argv + ["--privacy-share", "1"])[0]
        self.assertGreater(pure_privacy["metrics"]["offered"],
                           2 * pure_pq["metrics"]["offered"])
        self.assertEqual(pure_pq["variant"]["slot_length"], 1000.0)

    def test_the_variant_prices_what_the_flags_asked(self):
        # The calibration must be attributable from the row alone
        # (docs/pitfalls.md: a variant that omits a parameter cannot
        # attribute its own outcome), and a preset naming another cost
        # basis must not unprice the run.
        argv = SMALL + ["--tps", "2", "--privacy-share", "0.5",
                        "--leanstark-factor", "2", "--anchor-ms", "834"]
        record = bench_records(argv)[0]
        variant = record["variant"]
        self.assertEqual(variant["prove_cost_basis"], "circuit")
        self.assertEqual(variant["circuit_sphincs_hashes"],
                         mainnet.LEANVM_B_SPHINCS_CYCLES)
        self.assertEqual(variant["circuit_recursive_hashes"],
                         mainnet.LEANVM_B_RECURSIVE_CYCLES)
        self.assertEqual(variant["circuit_leanstark_hashes"],
                         2 * mainnet.LEANVM_B_RECURSIVE_CYCLES)
        # No trace ceiling is modelled; infinity is not JSON, so the strict
        # record shows null rather than a number that would read as a bound.
        self.assertIsNone(variant["circuit_trace_cap"])
        # The anchor reaches the run as ms per cycle: doubling the measured
        # fold time must double the rate exactly.
        base = bench_records(SMALL + ["--tps", "2", "--privacy-share", "0.5",
                                      "--anchor-ms", "417"])[0]
        self.assertAlmostEqual(
            variant["circuit_ms_per_hash"],
            2 * base["variant"]["circuit_ms_per_hash"])

    def test_cpu_utilisation_does_not_measure_the_drain(self):
        # Utilisation is read at the end of the birth window: read at the
        # horizon it would count the drain's idle time in its denominator
        # and fall as the drain grows (docs/pitfalls.md).
        argv = SMALL[:] + ["--tps", "4", "--privacy-share", "0.5"]
        argv[argv.index("--drain-slots") + 1] = "0"
        short = bench_records(argv)[0]
        argv[argv.index("--drain-slots") + 1] = "3"
        long = bench_records(argv)[0]
        self.assertEqual(short["metrics"]["cpu_load_window"],
                         long["metrics"]["cpu_load_window"])

    def test_repeating_a_command_reproduces_its_bytes(self):
        # The mix stream is a new source of randomness; the determinism
        # contract (CONTRIBUTING.md) requires byte-identical repetition, so
        # the raw stdout is compared, not parsed records.
        argv = SMALL + ["--tps", "3", "--privacy-share", "0.4"]
        self.assertEqual(bench_stdout(argv), bench_stdout(argv))

    def test_json_pairs_the_unchanged_terminal_mean_with_its_count(self):
        captured = []
        original = mainnet.one_run

        def capture(*args):
            outcome, sim, manifest = original(*args)
            captured.append(sim)
            return outcome, sim, manifest

        argv = SMALL + ["--tps", "4", "--privacy-share", "0"]
        with mock.patch.object(mainnet, "one_run", side_effect=capture):
            record = bench_records(argv)[0]
        sim = captured[0]
        metrics = record["metrics"]
        self.assertEqual(metrics["root_count"], mainnet.root_count(sim))
        self.assertEqual(metrics["root_claim"], mainnet.mean_root_claim(sim))
        self.assertIn("plan_roots_completed", metrics)
        self.assertIn("plan_root_claim_weighted_mean", metrics)
        self.assertNotIn("root_count", record["variant"])
        self.assertNotIn("root_stats", record["workload"])
        self.assertEqual(bench_stdout(argv),
                         bench_stdout(argv + ["--root-stats"]))

    def test_opt_in_table_prints_count_beside_claim_without_changing_values(self):
        argv = [value for value in SMALL if value != "--json"]
        argv += ["--tps", "4", "--privacy-share", "0"]
        original = bench_stdout(argv)
        extended = bench_stdout(argv + ["--root-stats"])
        record = bench_records(argv + ["--json"])[0]
        original_lines = original.splitlines()
        extended_lines = extended.splitlines()
        header_index = next(index for index, line in enumerate(extended_lines)
                            if "claim" in line and "proofs" in line)
        header = extended_lines[header_index].split()
        self.assertEqual(header[-3:], ["claim", "roots", "proofs"])
        row = next(line.split() for line in extended_lines[header_index + 1:]
                   if "eip_faithful" in line)
        self.assertEqual(float(row[-3]),
                         float(f"{record['metrics']['root_claim']:.1f}"))
        self.assertEqual(float(row[-2]), record["metrics"]["root_count"])
        self.assertEqual(extended, bench_stdout(argv + ["--root-stats"]))
        self.assertEqual(len(original_lines), len(extended_lines))
        # Only the header, its rule and the data row acquire the extra column.
        for index, (before, after) in enumerate(zip(original_lines,
                                                   extended_lines)):
            if index == header_index or set(after) == {"-"}:
                continue
            if "eip_faithful" in after:
                values = after.split()
                del values[-2]
                self.assertEqual(values, before.split())
            else:
                self.assertEqual(before, after)

    def test_explicit_ages_record_maturity_without_extending_the_run(self):
        argv = SMALL + ["--tps", "2", "--privacy-share", "0"]
        baseline = bench_records(argv)[0]
        measured = bench_records(argv + ["--outcome-ages-ms", "500,2000"])[0]
        self.assertEqual(measured["workload"]["outcome_ages_ms"], [500.0, 2000.0])
        self.assertEqual(baseline["metrics"]["fixed_age_outcomes"], -1)
        self.assertEqual(baseline["variant"], measured["variant"])
        self.assertNotIn("outcome_ages_ms", measured["variant"])
        summaries = measured["metrics"]["fixed_age_outcomes"]
        self.assertEqual([row["age_ms"] for row in summaries], [500.0, 2000.0])
        self.assertTrue(summaries[0]["comparable"])
        self.assertFalse(summaries[1]["comparable"])
        self.assertEqual(summaries[1]["late"]["mature_txs"], 1)
        self.assertEqual(summaries[1]["late"]["censored_txs"], 1)
        self.assertEqual(summaries[1]["all"]["observed_until_ms"], 3000.0)
        for key in baseline["metrics"]:
            if key != "fixed_age_outcomes":
                self.assertEqual(baseline["metrics"][key],
                                 measured["metrics"][key], key)
        self.assertEqual(bench_stdout(argv + ["--outcome-ages-ms", "500,2000"]),
                         bench_stdout(argv + ["--outcome-ages-ms", "500,2000"]))

    def test_fixed_age_and_utilisation_windows_do_not_follow_drain_length(self):
        argv = SMALL + ["--tps", "2", "--privacy-share", "0",
                        "--outcome-ages-ms", "500"]
        short = bench_records(argv)[0]["metrics"]
        long = bench_records(argv + ["--drain-slots", "3"])[0]["metrics"]
        self.assertEqual(short["load_utilisation"], long["load_utilisation"])
        self.assertEqual(short["load_traffic"], long["load_traffic"])
        traffic = short["load_traffic"]
        self.assertEqual(traffic["start_ms"], 0.0)
        self.assertEqual(traffic["end_ms"], 2000.0)
        self.assertGreater(traffic["attempted_bytes_per_second"], 0)
        self.assertAlmostEqual(
            traffic["wire_bytes_per_second"],
            short["load_utilisation"]["egress_utilisation"] * 25e6 / 8)
        window = short["load_utilisation"]
        self.assertEqual(window["start_ms"], 0.0)
        self.assertEqual(window["end_ms"], 2000.0)
        self.assertEqual(window["cpu_utilisation"], short["cpu_load_window"])
        for metrics in (short, long):
            for cohort in ("all", "early", "late"):
                metrics["fixed_age_outcomes"][0][cohort].pop("observed_until_ms")
        self.assertEqual(short["fixed_age_outcomes"], long["fixed_age_outcomes"])

    def test_age_requests_require_valid_numbers_and_json_output(self):
        argv = SMALL + ["--tps", "2", "--privacy-share", "0"]
        for ages in ("-1", "nan", "inf", "abc", "1,"):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    bench_records(argv + ["--outcome-ages-ms", ages])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                bench_stdout([value for value in argv if value != "--json"]
                             + ["--outcome-ages-ms", "500"])


if __name__ == "__main__":
    unittest.main()
