#!/usr/bin/env python3
"""The on-chain bytes a block would carry under EIP-8288's accounting.

The EIP prices a block's aggregation overhead as one recursive proof plus a
fixed term per proven statement. Nothing in the simulator sends those bytes:
blocks are chain facts, and block notices on the wire carry a proof plus
transaction hashes under a different encoding. This tool derives the on-chain
figure after the fact from each record's `block_observations`:

    overhead = size_recursive_proof + STATEMENT_BYTES * included_txs

It holds only while one simulated transaction is one statement, which is the
model today. `included_txs` is the block's newly included transactions; the
union of selected claims is deliberately not used, since a claim can name a
transaction the block did not include.

A block that includes nothing posts no proof by default; `--charge-empty`
applies the formula literally and charges the constant anyway. Neither choice
is a protocol fact -- the EIP does not say -- so the record names the one it
used. This is a derived byte estimate, not wire traffic and not gas.

    python3 -m bench.mainnet ... --json | python3 tools/onchain_overhead.py
    python3 tools/onchain_overhead.py --table records.jsonl
"""

import argparse
import json
import sys

# EIP-8288: 96 bytes per proven statement beside the block's proof.
STATEMENT_BYTES = 96


def block_overhead(record, statement_bytes=STATEMENT_BYTES, charge_empty=False):
    """Per-block and total assumed on-chain bytes for one bench record.

    Returns -1 fields when the record carries no block observations or no
    proof size, so an absent measurement is not read as zero overhead.
    """
    variant = record.get("variant", {})
    metrics = record.get("metrics", {})
    proof_bytes = variant.get("size_recursive_proof")
    blocks = metrics.get("block_observations")
    result = {
        "benchmark": record.get("benchmark"),
        "seed": record.get("seed"),
        "proof_bytes": proof_bytes if proof_bytes is not None else -1,
        "statement_bytes": statement_bytes,
        "empty_blocks_charged": charge_empty,
        "blocks": -1,
        "nonempty_blocks": -1,
        "included_txs": -1,
        "overhead_bytes": -1,
        "overhead_bytes_per_included_tx": -1.0,
        "per_block": -1,
    }
    if proof_bytes is None or not isinstance(blocks, list):
        return result
    per_block = []
    total = 0
    included = 0
    nonempty = 0
    for block in blocks:
        count = block["included_txs"]
        charged = count > 0 or charge_empty
        overhead = statement_bytes * count + (proof_bytes if charged else 0)
        per_block.append({"slot": block["slot"], "proposer": block["proposer"],
                          "included_txs": count, "proof_charged": charged,
                          "overhead_bytes": overhead})
        total += overhead
        included += count
        nonempty += count > 0
    result.update(blocks=len(blocks), nonempty_blocks=nonempty,
                  included_txs=included, overhead_bytes=total,
                  per_block=per_block)
    if included:
        result["overhead_bytes_per_included_tx"] = total / included
    return result


def _records(paths):
    streams = [open(path) for path in paths] if paths else [sys.stdin]
    for stream in streams:
        for line in stream:
            line = line.strip()
            if line:
                yield json.loads(line)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*",
                        help="JSON-lines bench records; stdin when omitted")
    parser.add_argument("--statement-bytes", type=int, default=STATEMENT_BYTES,
                        help=f"bytes per proven statement (EIP: {STATEMENT_BYTES})")
    parser.add_argument("--charge-empty", action="store_true",
                        help="charge the proof constant to blocks that include "
                             "nothing; the default charges no proof for them")
    parser.add_argument("--table", action="store_true",
                        help="print a text table instead of JSON lines")
    args = parser.parse_args(argv)
    if args.statement_bytes < 0:
        parser.error("--statement-bytes must be nonnegative")
    rows = [block_overhead(record, args.statement_bytes, args.charge_empty)
            for record in _records(args.paths)]
    if not args.table:
        for row in rows:
            print(json.dumps(row, sort_keys=True, separators=(",", ":")))
        return 0
    header = (f"{'benchmark':>10} {'seed':>4} {'blocks':>6} {'nonempty':>8} "
              f"{'included':>8} {'overhead MB':>11} {'B/tx':>8}")
    print(header)
    print("-" * len(header))
    for row in rows:
        per_tx = row["overhead_bytes_per_included_tx"]
        print(f"{str(row['benchmark']):>10} {str(row['seed']):>4} "
              f"{row['blocks']:>6} {row['nonempty_blocks']:>8} "
              f"{row['included_txs']:>8} "
              f"{row['overhead_bytes'] / 1e6:>11.3f} "
              f"{per_tx:>8.0f}")
    print()
    print(f"proof {rows[0]['proof_bytes'] if rows else -1} B + "
          f"{args.statement_bytes} B per included transaction; empty blocks "
          f"{'charged' if args.charge_empty else 'not charged'} the proof. "
          f"A derived estimate, not wire bytes and not gas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
