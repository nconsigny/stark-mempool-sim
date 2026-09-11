"""Arrival profiles share birth attributes; prefixes retain literal records."""

import argparse
from dataclasses import replace
import io
import json
import random
import unittest
from unittest import mock

from bench import mainnet
from bench.workload import (ARRIVAL_PROFILES, Birth, WorkloadManifest,
                            arrival_times)
from starksim.arena import KIND_PQ, KIND_PRIVACY

from tests.support import make_sim
from tests.test_mainnet import SMALL, bench_records, bench_stdout


def manifest(profile="uniform", count=100, seed=3):
    args = argparse.Namespace(slots=4, slot_length=1000.0, arrivals=profile)
    return mainnet.workload_manifest(args, count, 0.35, seed, (0, 1))


def births_in(sim):
    records = []
    for leaf in sim.arena.leaves:
        lifetime = None
        if leaf.expiry is not None:
            lifetime = leaf.expiry - leaf.born
        records.append(Birth(leaf.born, leaf.kind, leaf.fee, lifetime,
                             sim.entry_of[leaf.leaf_index]))
    return tuple(records)


class TestArrivalProfiles(unittest.TestCase):
    def test_uniform_preserves_legacy_spacing_kinds_fees_and_entries(self):
        value = manifest(count=17)
        entries = random.Random("3:entry")
        kinds = mainnet.mixed_kinds(17, 0.35, 3)
        expected = []
        for index, kind in enumerate(kinds):
            expected.append(Birth(index * (4000.0 / 17), kind,
                                   float(index % 5), None, entries.randrange(2)))
        self.assertEqual(value.births, tuple(expected))

    def test_all_profiles_preserve_the_non_time_attributes(self):
        records = []
        for profile in ARRIVAL_PROFILES:
            value = manifest(profile)
            records.append([(birth.kind, birth.fee, birth.lifetime_ms,
                             birth.node_id) for birth in value.births])
            self.assertEqual(value.metadata()["realised_mix"], {"q": 65, "p": 35})
        self.assertEqual(records[0], records[1])
        self.assertEqual(records[0], records[2])

    def test_poisson_is_repeatable_and_seed_sensitive(self):
        first, phase = arrival_times(100, 4000.0, 1000.0, "poisson", 7)
        self.assertIsNone(phase)
        self.assertEqual(first, arrival_times(100, 4000.0, 1000.0, "poisson", 7)[0])
        self.assertNotEqual(first, arrival_times(100, 4000.0, 1000.0, "poisson", 8)[0])
        self.assertEqual(len(first), 100)
        self.assertEqual(first, sorted(first))
        self.assertTrue(all(0 <= time < 4000.0 for time in first))
        self.assertNotEqual(first[1] - first[0], first[2] - first[1])
        rng = random.Random("7:arrivals:poisson:v1")
        self.assertEqual(first, sorted(rng.random() * 4000.0 for _ in range(100)))

    def test_burst_phase_is_independent_of_count_and_rate_is_fivefold(self):
        in_first_half = 0
        for seed in range(128):
            times, phase = arrival_times(100, 1000.0, 1000.0, "bursty", seed)
            phase_rng = random.Random(f"{seed}:arrivals:bursty-phase:v1")
            self.assertEqual(phase, phase_rng.random() * 1000.0)
            self.assertEqual(phase,
                             arrival_times(10, 1000.0, 1000.0, "bursty", seed)[1])
            positions = [(time - phase) % 1000.0 for time in times]
            self.assertTrue(all(0 <= position < 200.0 for position in positions))
            in_first_half += sum(position < 100.0 for position in positions)
            self.assertEqual(len(times) / 200.0, 5 * (len(times) / 1000.0))
        # A half-burst spans 100 ms per period. Its independent conditional
        # samples estimate the same peak rate without counting the entire
        # fixed population as the statistic.
        rate = in_first_half / (128 * 100.0)
        self.assertAlmostEqual(rate, 5 * (100 / 1000.0), delta=0.02)

    def test_partial_burst_generation_is_refused_but_prefixing_is_allowed(self):
        with self.assertRaises(ValueError):
            arrival_times(10, 1500.0, 1000.0, "bursty", 1)
        self.assertEqual(manifest("bursty").prefix(1500.0).window_ms, 1500.0)

    def test_invalid_arrival_parameters_are_refused(self):
        for count, window, period, profile in (
                (-1, 1000, 100, "uniform"), (2, 0, 100, "uniform"),
                (2, 1000, 0, "poisson"), (2, 1000, 100, "unknown")):
            with self.assertRaises(ValueError):
                arrival_times(count, window, period, profile, 1)


class TestManifest(unittest.TestCase):
    def test_json_roundtrip_and_detached_records(self):
        original = manifest("bursty")
        record = original.to_record()
        restored = WorkloadManifest.from_record(json.loads(json.dumps(record)))
        self.assertEqual(original, restored)
        record["births"][0]["node_id"] = 100
        record["realised_mix"]["q"] = 0
        self.assertEqual(original.metadata()["realised_mix"]["q"], 65)
        self.assertLess(original.births[0].node_id, 2)

    def test_prefix_replays_exactly_the_same_births_and_its_own_mix(self):
        for profile in ARRIVAL_PROFILES:
            with self.subTest(profile=profile):
                parent = manifest(profile)
                prefix = parent.prefix(1750.0)
                self.assertEqual(prefix.births,
                                 tuple(b for b in parent.births if b.time_ms < 1750))
                self.assertEqual(prefix.prefix(500), parent.prefix(500))
                full_sim = make_sim(relay_policy="wrapper_only")
                short_sim = make_sim(relay_policy="wrapper_only")
                parent.schedule(full_sim)
                prefix.schedule(short_sim)
                full_sim.run(until=1749.0)
                short_sim.run(until=1749.0)
                self.assertEqual(births_in(full_sim), births_in(short_sim))
                short_sim.run(until=5000.0)
                self.assertEqual(births_in(short_sim), prefix.births)
                mix = prefix.metadata()["realised_mix"]
                self.assertEqual(sum(mix.values()), len(prefix.births))
                self.assertEqual(mix["p"], sum(b.kind == KIND_PRIVACY
                                               for b in prefix.births))
                self.assertEqual(prefix.generation_count, parent.generation_count)
                self.assertEqual(prefix.phase_ms, parent.phase_ms)

    def test_prefix_does_not_rebalance_or_rebase_births(self):
        original = WorkloadManifest(
            (Birth(10.0, KIND_PRIVACY, 7.0, 5.0, 1),
             Birth(20.0, KIND_PQ, 2.0, None, 0)),
            "uniform", 100.0, 100.0, None, (0, 1), 100.0, 2)
        prefix = original.prefix(20.0)
        self.assertEqual(prefix.births, original.births[:1])
        self.assertEqual(prefix.metadata()["realised_mix"], {"q": 0, "p": 1})
        sim = make_sim(relay_policy="wrapper_only")
        prefix.schedule(sim)
        sim.run(until=100.0)
        self.assertEqual(births_in(sim), prefix.births)
        self.assertEqual(sim.expiry_times, {0: 15.0})
        self.assertEqual(original.prefix(5.0).metadata()["realised_mix"],
                         {"q": 0, "p": 0})

    def test_invalid_manifests_are_refused(self):
        original = manifest()
        for changes in ({"version": 2}, {"births": tuple(reversed(original.births))},
                        {"window_ms": 1.0}, {"phase_ms": 0.0},
                        {"entry_nodes": (0,)}, {"generation_count": 1}):
            with self.assertRaises(ValueError):
                replace(original, **changes)
        record = original.to_record()
        record["realised_mix"]["p"] += 1
        with self.assertRaises(ValueError):
            WorkloadManifest.from_record(record)


class TestMainnetManifest(unittest.TestCase):
    def test_uniform_run_and_trace_match_the_legacy_scheduler_record_for_record(self):
        argv = SMALL + ["--tps", "4", "--privacy-share", "0.5"]

        def legacy_schedule(value, sim):
            kinds = mainnet.mixed_kinds(8, 0.5, sim.seed)
            spacing = 2000.0 / 8
            for index, kind in enumerate(kinds):
                sim.schedule_birth(index * spacing, kind, fee=float(index % 5))

        streams = []
        original = mainnet.NetworkSim

        def traced(*args, **kwargs):
            stream = io.StringIO()
            streams.append(stream)
            return original(*args, **kwargs, trace=stream)

        with mock.patch.object(mainnet, "NetworkSim", side_effect=traced):
            actual = bench_stdout(argv)
            with mock.patch.object(WorkloadManifest, "schedule", legacy_schedule):
                expected = bench_stdout(argv)
        self.assertEqual(actual, expected)
        self.assertEqual(streams[0].getvalue(), streams[1].getvalue())

    def test_policy_and_cost_rows_consume_the_same_manifest(self):
        seen = []
        original = mainnet.one_run

        def capture(*args):
            result = original(*args)
            seen.append(result[2])
            self.assertEqual(births_in(result[1]), result[2].births)
            return result

        argv = SMALL + ["--tps", "4", "--privacy-share", "0.5", "--arrivals", "bursty",
                        "--presets", "eip_faithful,high_throughput",
                        "--anchor-ms", "417,1000",
                        "--emit-manifest"]
        with mock.patch.object(mainnet, "one_run", side_effect=capture):
            rows = bench_records(argv)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(value is seen[0] for value in seen))
        for row in rows:
            workload = row["workload"]
            self.assertEqual(workload["manifest"], seen[0].to_record())
            self.assertEqual(workload["realised_mix"], {"q": 4, "p": 4})
            self.assertEqual(workload["manifest_version"], 1)
            self.assertNotIn("emit_manifest", workload)
            for key in seen[0].metadata():
                self.assertNotIn(key, row["variant"])
                self.assertNotIn(key, row["metrics"])

    def test_json_manifest_is_opt_in_and_does_not_change_measurements(self):
        for profile in ARRIVAL_PROFILES:
            argv = SMALL + ["--tps", "4", "--privacy-share", "0.5",
                            "--arrivals", profile]
            plain = bench_records(argv)
            emitted = bench_records(argv + ["--emit-manifest"])
            for row in emitted:
                row["workload"].pop("manifest")
            self.assertEqual(plain, emitted)
            self.assertEqual(bench_stdout(argv), bench_stdout(argv))

    def test_manifest_output_requires_json(self):
        argv = [value for value in SMALL if value != "--json"]
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                bench_stdout(argv + ["--emit-manifest"])

    def test_mainnet_consumes_a_supplied_prefix_without_regeneration(self):
        calls = []
        original = mainnet.one_run

        def capture(*args):
            result = original(*args)
            calls.append((args, result[2]))
            return result

        with mock.patch.object(mainnet, "one_run", side_effect=capture):
            bench_records(SMALL + ["--tps", "4", "--privacy-share", "0.5",
                                   "--arrivals", "poisson"])
        args, parent = calls[0]
        prefix = parent.prefix(750.0)
        with mock.patch.object(mainnet, "workload_manifest",
                               side_effect=AssertionError("must consume the prefix")):
            outcome, sim, consumed = original(*args[:-1], prefix)
        self.assertIs(consumed, prefix)
        self.assertEqual(births_in(sim), prefix.births)
        self.assertEqual(outcome["arrived"], len(prefix.births))
        self.assertEqual(outcome["load_utilisation"]["end_ms"], 750.0)


if __name__ == "__main__":
    unittest.main()
