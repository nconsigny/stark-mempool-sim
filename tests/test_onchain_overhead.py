"""On-chain overhead is derived from inclusion, never from claims or notices."""

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from tools import onchain_overhead
from tools.onchain_overhead import STATEMENT_BYTES, block_overhead

from tests.test_seed_start import output


def record(blocks, proof=262144):
    return {"benchmark": "fixture", "seed": 3,
            "variant": {"size_recursive_proof": proof},
            "metrics": {"block_observations": [
                {"slot": index + 1, "proposer": 0, "included_txs": count,
                 "selected_claim_union_txs": count + 5}
                for index, count in enumerate(blocks)]}}


class TestBlockOverhead(unittest.TestCase):
    def test_formula_uses_included_not_claimed_and_skips_empty_by_default(self):
        result = block_overhead(record([4, 0, 1]))
        self.assertEqual(result["blocks"], 3)
        self.assertEqual(result["nonempty_blocks"], 2)
        self.assertEqual(result["included_txs"], 5)
        self.assertEqual([b["overhead_bytes"] for b in result["per_block"]],
                         [262144 + 4 * 96, 0, 262144 + 96])
        self.assertEqual(result["overhead_bytes"], 2 * 262144 + 5 * 96)
        self.assertEqual(result["overhead_bytes_per_included_tx"],
                         (2 * 262144 + 5 * 96) / 5)
        self.assertFalse(result["per_block"][1]["proof_charged"])
        self.assertEqual(STATEMENT_BYTES, 96)

    def test_charge_empty_applies_the_formula_literally(self):
        result = block_overhead(record([4, 0]), charge_empty=True)
        self.assertEqual(result["per_block"][1]["overhead_bytes"], 262144)
        self.assertTrue(result["empty_blocks_charged"])
        self.assertEqual(result["overhead_bytes"], 2 * 262144 + 4 * 96)

    def test_missing_observations_or_proof_size_are_unavailable_not_zero(self):
        for broken in ({"variant": {}, "metrics": {"block_observations": []}},
                       {"variant": {"size_recursive_proof": 1},
                        "metrics": {}}):
            result = block_overhead(broken)
            self.assertEqual(result["overhead_bytes"], -1)
            self.assertEqual(result["per_block"], -1)
        empty = block_overhead(record([]))
        self.assertEqual(empty["blocks"], 0)
        self.assertEqual(empty["overhead_bytes"], 0)
        self.assertEqual(empty["overhead_bytes_per_included_tx"], -1.0)

    def test_cli_reads_bench_json_and_reconciles_with_inclusion(self):
        lines = output("mainnet", "--json").splitlines()
        stream = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("\n".join(lines))):
            with contextlib.redirect_stdout(stream):
                onchain_overhead.main([])
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(len(rows), len(lines))
        for row, line in zip(rows, lines):
            source = json.loads(line)
            included = sum(b["included_txs"] for b in
                           source["metrics"]["block_observations"])
            self.assertEqual(row["included_txs"], included)
            self.assertEqual(row["proof_bytes"],
                             source["variant"]["size_recursive_proof"])
            self.assertEqual(row["overhead_bytes"],
                             row["nonempty_blocks"] * row["proof_bytes"]
                             + 96 * included)
        table = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("\n".join(lines))):
            with contextlib.redirect_stdout(table):
                onchain_overhead.main(["--table"])
        self.assertIn("not charged", table.getvalue())
        self.assertIn("not wire bytes and not gas", table.getvalue())


if __name__ == "__main__":
    unittest.main()
