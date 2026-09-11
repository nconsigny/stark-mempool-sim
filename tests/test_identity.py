"""Run identity describes constructed inputs without re-running generators."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

from bench import v1_calibration
from bench.common import random_regular_overlay
from starksim.costs import (BUILD_BLOCK, PROVE_RECURSIVE, VERIFY_RECV,
                            CircuitCost, CostTable, LinearCost, NativeVerifyCost)
from starksim.identity import (IDENTITY_COLUMNS, code_version, cost_table_profile,
                               restore_cost_table, run_identity,
                               topology_fingerprint)
from starksim.netsim import NetworkSim, csv_header, csv_row
from starksim.network import Overlay
from starksim.trace import SCHEMA_VERSION

from tests.support import two_node_overlay
from tests.test_seed_start import FIXTURES, output


class TestTopologyIdentity(unittest.TestCase):
    def test_same_overlay_and_seed_reconstruction_agree_different_seed_differs(self):
        overlay = random_regular_overlay(12, 4, 10, 7)
        first = NetworkSim(overlay)
        second = NetworkSim(overlay, seed=99)
        regenerated = NetworkSim(random_regular_overlay(12, 4, 10, 7))
        different = NetworkSim(random_regular_overlay(12, 4, 10, 8))
        self.assertEqual(first.topology_fingerprint, second.topology_fingerprint)
        self.assertEqual(first.topology_fingerprint, regenerated.topology_fingerprint)
        self.assertNotEqual(first.topology_fingerprint, different.topology_fingerprint)
        self.assertEqual(overlay.latency_model._by_edge, {})

    def test_hash_covers_latencies_and_isolated_nodes_not_just_edge_names(self):
        line = [[1], [0, 2], [1]]
        first = Overlay.from_adjacency(line, base_latency=10)
        faster = Overlay.from_adjacency(line, base_latency=9)
        extra = Overlay.from_adjacency(line + [[]], base_latency=10)
        self.assertNotEqual(topology_fingerprint(first), topology_fingerprint(faster))
        self.assertNotEqual(topology_fingerprint(first), topology_fingerprint(extra))
        permuted = Overlay.from_adjacency([[1], [2, 0], [1]], base_latency=10)
        self.assertEqual(topology_fingerprint(first), topology_fingerprint(permuted))

    def test_cache_warming_does_not_change_fingerprint_or_identity_warm_cache(self):
        overlay = random_regular_overlay(8, 2, 10, 7)
        before = topology_fingerprint(overlay)
        overlay.latency(0, overlay.peers(0)[0])
        cache = dict(overlay.latency_model._by_edge)
        self.assertEqual(topology_fingerprint(overlay), before)
        self.assertEqual(overlay.latency_model._by_edge, cache)

    def test_unknown_latency_is_not_called(self):
        class StatefulLatency:
            def latency(self, source, target):
                raise AssertionError("identity must not execute arbitrary latency")

        self.assertEqual(topology_fingerprint(Overlay([[1], [0]], StatefulLatency())), -1)


class TestCostIdentity(unittest.TestCase):
    def test_default_and_custom_tables_round_trip_parameters_and_stream(self):
        circuit = CircuitCost(0.25, overhead_hashes=2, sphincs_hashes=3,
                              leanstark_hashes=4, recursive_hashes=5,
                              trace_rows_per_hash=6, trace_cap=700)
        tables = [CostTable(), CostTable(verify_recv=2, prove_recursive=8,
                                        build_block=3, jitter_frac=0.2, seed=17),
                  CostTable(verify_recv=LinearCost(3), prove_recursive=circuit,
                            build_block=4, jitter_frac=0.125, seed=19)]
        for table in tables:
            profile = json.loads(json.dumps(cost_table_profile(table)))
            restored = restore_cost_table(profile)
            self.assertEqual(cost_table_profile(restored), profile)
            for kind in (VERIFY_RECV, PROVE_RECURSIVE, BUILD_BLOCK):
                for size in (1, 3, 100):
                    self.assertEqual(table.duration(kind, size),
                                     restored.duration(kind, size))
            clone = table.for_basis("inputs")
            self.assertEqual(clone.seed, table.seed)
            self.assertEqual(clone._rng.getstate(), table._rng.getstate())
        fields = cost_table_profile(tables[-1])["costs"][PROVE_RECURSIVE]["parameters"]
        self.assertEqual(fields, {"ms_per_hash": 0.25, "overhead_hashes": 2,
                                  "sphincs_hashes": 3, "leanstark_hashes": 4,
                                  "recursive_hashes": 5,
                                  "trace_rows_per_hash": 6, "trace_cap": 700})

    def test_unbounded_trace_cap_survives_the_emitted_json(self):
        from bench.common import _json_value
        import math
        table = CostTable(prove_recursive=CircuitCost(0.5, trace_cap=math.inf))
        profile = cost_table_profile(table)
        self.assertIsNone(profile["costs"][PROVE_RECURSIVE]["parameters"]["trace_cap"])
        emitted = json.loads(json.dumps(_json_value(profile), allow_nan=False))
        self.assertEqual(emitted, profile)
        restored = restore_cost_table(emitted)
        self.assertEqual(restored.costs[PROVE_RECURSIVE].trace_cap, math.inf)
        self.assertTrue(restored.costs[PROVE_RECURSIVE].fits(2, 0, 0))
        self.assertEqual(cost_table_profile(restored), profile)

    def test_native_profile_and_distinct_cost_seed_are_preserved(self):
        sim = NetworkSim(two_node_overlay(), seed=99,
                         costs=CostTable(prove_recursive=CircuitCost(0.1),
                                         jitter_frac=0.25, seed=13),
                         verify_cost=NativeVerifyCost(1, 2, 3),
                         prove_cost_basis="circuit")
        profile = run_identity(sim, {}, "test")["cost_profile"]
        self.assertEqual(profile["table"]["seed"], 13)
        self.assertEqual(profile["table"]["jitter_frac"], 0.25)
        self.assertEqual(profile["execution_basis"], "circuit")
        self.assertEqual(profile["selection_table"]["prove_cost_basis"], "circuit")
        self.assertEqual(profile["native_verify"],
                         {"per_signature": 1, "per_stark": 2, "per_message": 3})
        self.assertTrue(profile["complete"])

    def test_arbitrary_callable_is_unavailable_and_never_probed(self):
        sim = NetworkSim(two_node_overlay())

        def opaque(size):
            raise AssertionError("identity called a cost")

        sim.costs.costs[PROVE_RECURSIVE] = opaque
        profile = run_identity(sim, {}, "test")["cost_profile"]
        self.assertFalse(profile["complete"])
        self.assertEqual(profile["table"]["costs"][PROVE_RECURSIVE],
                         {"type": "unavailable", "parameters": -1})
        with self.assertRaisesRegex(ValueError, "unavailable"):
            restore_cost_table(profile["table"])

    def test_linear_cost_preserves_multiplication_and_jitter_sequence(self):
        for rate in (0.0, 0.1, 8.75):
            old = CostTable(prove_recursive=lambda size: rate * size,
                            jitter_frac=0.3, seed=41)
            new = CostTable(prove_recursive=LinearCost(rate),
                            jitter_frac=0.3, seed=41)
            for size in (0, 1, 3, 100, 1025):
                self.assertEqual(old.duration(PROVE_RECURSIVE, size),
                                 new.duration(PROVE_RECURSIVE, size))
            self.assertEqual(old._rng.getstate(), new._rng.getstate())


class TestGitIdentity(unittest.TestCase):
    def test_loose_detached_packed_and_missing_head(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(code_version(root), -1)
            git = root / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n")
            (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
            self.assertEqual(code_version(root), "a" * 40)
            (git / "HEAD").write_text("b" * 40 + "\n")
            self.assertEqual(code_version(root), "b" * 40)
            (git / "HEAD").write_text("ref: refs/heads/packed\n")
            (git / "packed-refs").write_text("c" * 40 + " refs/heads/packed\n")
            self.assertEqual(code_version(root), "c" * 40)
            (git / "HEAD").write_text("ref: refs/../outside\n")
            self.assertEqual(code_version(root), -1)
            (git / "HEAD").write_text("not a commit\n")
            self.assertEqual(code_version(root), -1)

    def test_linked_worktree_uses_its_head_and_common_ref(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            common = root / "common"
            admin = common / "worktrees" / "test"
            admin.mkdir(parents=True)
            (root / ".git").write_text("gitdir: common/worktrees/test\n")
            (admin / "commondir").write_text("../..\n")
            (admin / "HEAD").write_text("ref: refs/heads/test\n")
            (common / "refs" / "heads").mkdir(parents=True)
            (common / "refs" / "heads" / "test").write_text("d" * 40 + "\n")
            self.assertEqual(code_version(root), "d" * 40)


class TestRunIdentity(unittest.TestCase):
    def test_repeated_identity_does_not_change_events_rng_trace_or_latency_cache(self):
        stream = io.StringIO()
        sim = NetworkSim(random_regular_overlay(8, 2, 10, 9), trace=stream,
                         costs=CostTable(prove_recursive=5, jitter_frac=0.2, seed=17))
        sim.schedule_birth(0, "q")
        sim.schedule_ticks(2)
        sim.run(until=100)
        before = (sim.engine.events_processed, len(sim.engine.calendar),
                  sim.engine.calendar.peek_time(), sim.engine.now,
                  sim.rng_entry.getstate(), sim.costs._rng.getstate(),
                  dict(sim.overlay.latency_model._by_edge), stream.getvalue())
        first = run_identity(sim, {}, "test")
        self.assertEqual(first, run_identity(sim, {}, "test"))
        after = (sim.engine.events_processed, len(sim.engine.calendar),
                 sim.engine.calendar.peek_time(), sim.engine.now,
                 sim.rng_entry.getstate(), sim.costs._rng.getstate(),
                 dict(sim.overlay.latency_model._by_edge), stream.getvalue())
        self.assertEqual(before, after)
        self.assertEqual(first["trace_schema_version"], SCHEMA_VERSION)
        first["node_assignment"]["entry_nodes"].clear()
        self.assertTrue(run_identity(sim, {}, "test")["node_assignment"]["entry_nodes"])

    def test_assignment_and_generator_versions(self):
        for mode in ("fixed", "rotating", "random", "dedicated"):
            sim = NetworkSim(random_regular_overlay(8, 2, 1, 3),
                             proposer_mode=mode, proposer_id=2, entry_subset=3)
            identity = run_identity(sim, {}, "fixture")
            assignment = identity["node_assignment"]
            self.assertEqual(assignment["entry_nodes"], list(sim.entry_nodes))
            self.assertEqual(assignment["proposer"]["mode"], mode)
            self.assertEqual(assignment["proposer"]["fixed_id"], 2)
            self.assertEqual(assignment["dedicated_builder"],
                             2 if mode == "dedicated" else -1)
            self.assertEqual(identity["workload_generator_version"], "legacy:fixture:v1")
        sim.workload_generator_version = 1
        self.assertEqual(run_identity(sim, {"manifest_version": 1}, "mainnet")[
            "workload_generator_version"], 1)
        with self.assertRaisesRegex(ValueError, "disagrees"):
            run_identity(sim, {"manifest_version": 2}, "mainnet")

    def test_every_bench_json_has_full_identity_and_scalar_metrics_agree(self):
        for name in FIXTURES:
            with self.subTest(bench=name):
                for line in output(name, "--json").splitlines():
                    record = json.loads(line)
                    identity = record["identity"]
                    self.assertTrue(identity["cost_profile"]["complete"])
                    for column in IDENTITY_COLUMNS:
                        self.assertEqual(record["metrics"][column], identity[column])
                    if name == "mainnet":
                        self.assertEqual(identity["workload_generator_version"],
                                         record["workload"]["manifest_version"])
                    else:
                        self.assertEqual(identity["workload_generator_version"],
                                         f"legacy:{name}:v1")

    def test_csv_suffix_and_direct_unknown_generator(self):
        sim = NetworkSim(two_node_overlay())
        self.assertEqual(csv_header().split(",")[-2:], list(IDENTITY_COLUMNS))
        fields = csv_row(sim.metrics()).split(",")
        self.assertEqual(fields[-2:], [sim.topology_fingerprint, "-1"])
        # The commit id never enters the CSV: it would change every row with
        # every commit and break byte-for-byte reproduction of a baseline.
        self.assertNotIn("simulator_code_version", csv_header().split(","))
        self.assertNotIn("simulator_code_version", sim.metrics())

    def test_retired_calibration_json_also_has_identity(self):
        stream = io.StringIO()
        argv = ["v1_calibration", "--json", "--aggregators", "2", "--slots", "1",
                "--slot-length", "100", "--lam-pq", "1", "--lam-priv", "0",
                "--supersede-policy", "keep_all"]
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(stream):
                v1_calibration.main()
        record = json.loads(stream.getvalue())
        self.assertEqual(record["identity"]["workload_generator_version"],
                         "legacy:v1_calibration:v1")
        self.assertTrue(record["identity"]["cost_profile"]["complete"])

    def test_code_version_is_read_when_identity_is_queried(self):
        sim = NetworkSim(two_node_overlay())
        with mock.patch("starksim.identity.code_version", return_value="a" * 40):
            self.assertEqual(run_identity(sim, {}, "test")["simulator_code_version"],
                             "a" * 40)
        real = run_identity(sim, {}, "test")["simulator_code_version"]
        self.assertTrue(real == -1 or len(real) in (40, 64))


if __name__ == "__main__":
    unittest.main()
