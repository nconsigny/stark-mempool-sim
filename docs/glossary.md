# Glossary

## Model terms

**Aggregate**
: A pool object produced by folding one or more objects. Its structural
  coverage and the claim its proof asserts can differ.

**Claim**
: The transaction mask another participant may rely on. An aggregate's claim
  is frozen when proving starts after removing what the prover then knows is
  discarded.

**Aggregator**
: A node assigned to create mempool recursive proofs through selection and
  folding. All nodes have this role by default; partial participation assigns
  the remainder to the relay node role.

**Relay (node role)**
: A node that spreads raw transactions and verifies and admits proofs, but
  creates no mempool proof. At decisions it forwards held proofs under its
  forwarding rule. It still builds blocks when assigned as proposer. This
  role is distinct from `relay_policy`, which governs raw-object propagation.

**Forwarding rule**
: The relay node's proof choice: `best` re-wraps the recursive proof with most
  locally live claims, while `all` forwards pending recursive proofs to peers
  without an accepted copy or a belief that they hold it. Refused copies are
  retried at a later decision, without refusal itself triggering a tickless
  decision. `none` forwards no proof; with one aggregator that proposes, it is
  builder-only aggregation. No rule creates a proof.

**Coverage**
: The immutable set of leaf transactions structurally folded into an object.
  It is global provenance, not proof of what a node knows or what an aggregate
  currently claims.

**`discards`**
: The EIP circuit input represented by removing locally known included or
  expired transactions from a new proof's claim. It does not mutate the
  aggregate's structural coverage.

**Leaf**
: One user transaction and one bit in every coverage mask. Kind `q` is a bare
  post-quantum signature and kind `p` is a privacy transaction with a STARK.

**Leaf fold**
: One direct leaf input to a completed proving job, including an internal
  job of a tree plan or a job whose plan was abandoned. Folding an aggregate
  does not count its contained leaves again. A job still in service has no
  completed leaf folds yet.

**First fold**
: The first completed job anywhere in the network to fold a particular leaf.
  Completions at the same timestamp follow the calendar's insertion order.

**Repeat fold**
: Every later completed job folding that leaf directly, including another
  job on the same node. Receptions and receive verification do not count.
  This is global duplication, not necessarily reusable work: a repeating
  node may have started before the first completion or never received that
  node's proof.

**Witness fold**
: One raw witness verified by a completed proving job: a direct leaf or one
  dependency carried by a witness-backed mode 0 aggregate input. Recursive
  proof inputs contribute no witness folds. `witness_folds` has its own
  first-fold and repeat-fold history, including both forms of raw input;
  `leaf_folds` retains its direct-object meaning.

**Leaf multiplicity**
: The number of distinct nodes that completed a direct fold of a leaf,
  conditioned on that leaf having been folded at least once. Repeated folds
  on one node increase fold events without increasing multiplicity. See the
  [leaf-fold measurements](#leaf-fold-measurements) for service attribution.

**Reach**
: The fraction or set of nodes that have ever admitted an object. Reach is
  cumulative and does not decrease.

**Residency**
: The fraction or set of nodes holding an object at a particular time.
  Supersession, pruning, or eviction can make residency lower than reach.

**Witness-backed claim**
: A claim a node received as the EIP's mode 0 — its dependencies' raw witnesses —
  rather than as a recursive proof. Recorded in `NodeView.witness_backed`: it
  cannot resolve a proof reference, forms no peer belief that a proof is held,
  and is folded at the price of its dependencies rather than of one nested proof.

**Wrapper constant**
: The recursive-proof term sent per peer per wrapper emission: 262,144 bytes
  under the default `SizeModel`, before dependency triples, bodies, or hashes.
  It is constant in the number of transactions but scales with node degree and
  inverse tick interval.

**Variant**
: The configuration identity returned by `NetworkSim.variant` and included in
  CSV rows and trace headers. It identifies protocol, network, cost, size, and
  bound choices, but not the workload scheduled by a harness.

**Preset**
: A named dict of `NetworkSim` keyword arguments in `starksim.presets`:
  `eip_faithful`, `v1_equivalent`, `high_throughput`. A preset names policy
  fields only; the overlay, size model and cost table stay with the workload.

**Form (of evidence)**
: How a node knows a transaction, read from `knw.o`: through the
  transaction's own object, through a wrapper claiming it, through an
  announcement (class `requested`), or through a block notice (`settled`).

**Spread tree**
: For one followed transaction, the edges from `knw.peer` to `knw.n` at each
  node's first transition out of `none`, rooted at the entry. Drawn by the
  viewer when colouring by knowledge; a record of successful spread only.

**Offered bytes / wire bytes**
: Offered bytes entered an egress queue; wire bytes have crossed node egress,
  including the transmitted part of a send still in service. Accepted work
  still waiting or transmitting accounts for the difference. Refused sends
  are not in either total.

**Attempted demand**
: Every call to send a payload, counted before egress admission. In `metrics()`
  and bench `--json` records, `attempted_bytes` equals `offered_bytes` plus
  `egress_refused_bytes`; the same identity holds for message counts.
  Retries and redundant copies are attempts too, so this is protocol-generated
  demand, not the exogenous transaction birth rate. Suppressed sends are not
  attempts.

**Egress refusal / CPU refusal**
: An egress refusal rejects a send before transmission. A CPU refusal rejects
  received evidence when its verification cannot enter the CPU queue: those
  bytes have already arrived. `egress_refused_bytes` and `cpu_refused_bytes`
  partition `dropped_bytes`; their message counts partition `dropped_messages`.
  These count message refusals, not refused proving or block-building jobs.

  Each `attempted`, `egress_refused` and `cpu_refused` metric prefix has
  `bytes` and `messages` totals, with `_by_kind`, `_by_node` and
  `_by_node_kind` breakdowns. Kinds are payload kinds throughout; node arrays
  are indexed by sender for attempts and egress refusals, and by receiver for
  CPU refusals. Snapshots are detached from subsequent traffic. These event
  counts are defined in every run, so no refusals is zero even without queue
  bounds; it is not a measurement of an unmodelled capacity limit.

## Aggregation measurements

**Completed plan root**
: The final proof of one aggregation plan, counted when that job completes.
  Internal proofs and re-wrapping an existing aggregate are not additional
  roots. A completed root with an empty frozen claim still counts, even though
  it emits no wrapper. `plan_roots_completed` is cumulative; consuming or
  retiring its output does not subtract it. `wrappers_emitted` instead counts
  wrapper cores. The `wrapper` entry in `attempted_messages_by_kind` counts
  per-peer attempts; subtract the corresponding
  `egress_refused_messages_by_kind` entry to obtain accepted copies.

**Terminal DAG root**
: An aggregate whose `consumed` count is zero at the observation instant.
  Consuming it as a fold input removes it from this population before the next
  proof completes. An unused internal output can belong to it even when its
  plan never completes. `bench.mainnet.mean_root_claim` and JSON `root_claim`
  retain this meaning; `root_count` counts the same population. Their legacy
  empty-population mean is zero, identified by a zero count. It is not the
  completion-time statistic below.

**Object-weighted and claim-occurrence-weighted root means**
: For each completed plan root, let `c` be its frozen claim's transaction
  count. `plan_root_claim_sum` is `sum(c)` and
  `plan_root_claim_square_sum` is `sum(c*c)`. `plan_root_claim_mean` divides
  the first by `plan_roots_completed`; `plan_root_claim_weighted_mean`
  divides the second by the first. Both accompany the root count and return
  `-1` when their denominator is zero. The weighted mean weights claim
  occurrences, not unique transactions: overlapping roots repeat a
  transaction's contribution.

  `plan_root_claim_histogram` has a zero bucket, power-of-two buckets with
  inclusive `minimum` and `maximum`, and an overflow bucket starting at
  `2**20` with `maximum: null`. Each bucket carries its `count`. The number
  of buckets is fixed; the exact moments do not use bucket approximations.

**Completed-root claim union**
: `plan_root_claim_union_txs` counts distinct transactions asserted by any
  completed plan root. `plan_root_live_claim_union_txs` removes canonical
  inclusions and expiries at the observation instant. This is a global
  historical union filtered for liveness, not evidence currently held by a
  particular node or available to a proposer. Neither union sums overlaps.

**Block marginal contribution**
: `block_observations` contains one decision record for each
  `NetworkSim._build_block` call, identified by `slot`, `time` in ms and
  `proposer`. Each `selected` entry carries its `object_id`, raw `claim_txs`,
  and `effective_txs` after local known discards and the configured body
  requirement. In pick order, `marginal_attempted_txs` removes transactions
  already supplied by earlier picks; `marginal_included_txs` counts the part
  actually newly included. Their sums equal the record's `attempted_txs` and
  `included_txs`. `selected_objects` counts the entries;
  `included_per_selected_object` divides inclusion by that count, or is `-1`
  for an empty selection.

  `selected_claim_union_txs` splits into `selected_known_discarded_txs`,
  `selected_missing_body_txs` and `attempted_txs` under `body_required`:
  missing-body counts exclude locally known discards. `stale_attempted_txs`
  is attempted minus newly included under delayed notification; it is `-1`
  under instant notification, where stale selection cannot be measured.
  Both `selected_missing_body_txs` and `candidate_missing_body_txs` are `-1`
  under `claim_only`, which disables that exclusion.

**Assumed on-chain overhead**
: `tools/onchain_overhead.py` derives, from each bench record's
  `block_observations`, the bytes a block would carry under the EIP's
  accounting: `size_recursive_proof + 96 x included_txs`, per block and in
  total, with the bytes per included transaction. It uses the block's newly
  included count, never a claim union, and by default charges no proof to a
  block that includes nothing; `--charge-empty` applies the formula
  literally, and the record says which was used. Valid only while one
  simulated transaction is one statement. It is a derived byte estimate
  from settlement, not wire traffic (block notices carry a different
  encoding) and not gas. `-1` when a record has no block observations or
  no proof size.

**Proposer candidate union and remaining backlog**
: A block record's `candidate_objects` counts extant objects in the proposer's
  held set, not objects elsewhere in the arena. `candidate_claim_union_txs`
  counts their raw claim union; `candidate_live_claim_union_txs` removes
  canonical discards at decision time. `candidate_effective_txs` instead
  applies local known discards and the configured body requirement, and
  `candidate_includable_txs` additionally removes canonical discards. The last
  union partitions into newly included and `remaining_includable_txs`.

  `budget_full_with_live_backlog` says that selection used all
  `block_budget + 1` object positions and left additional locally includable
  transactions. It does not establish that an optimal selection would need
  more positions. Candidate fields and this flag are `-1` when a nonpositive
  object limit bypasses the candidate scan. All records retain pre-settlement
  observations as counts, independent of later holdings or metric reads.

## Leaf-fold measurements

`metrics()["leaf_folds"]` observes direct leaf objects;
`metrics()["witness_folds"]` also observes the raw witnesses carried by mode 0
aggregate inputs. Both report `total` and `by_kind` (`pq`, `privacy`).
Each has `leaves_folded`, `fold_events`, and
`repeat_events = fold_events - leaves_folded`. `multiplicity` contains the
mean, exact nearest-rank p50 and p95, max, and a fixed `histogram` of
`minimum`, `maximum`, `count` records: 1, 2, 3–4, 5–8, 9–16, 17–32, 33–64,
65–128, 129–256, 257–512, 513+ (the last maximum is `null`). Empty populations
have zero counts and `-1` for mean, percentiles and max. The arrays
`fold_events_by_node`, `repeat_events_by_node` and
`repeat_service_ms_by_node` have one entry per node in node order.
Unfamiliar leaf kinds increment `unsupported_leaf_events` and receive no
service attribution; they are outside the supported kinds' counts and
multiplicity statistics, and never interrupt execution.

Under `prove_cost_basis="circuit"`, each signature witness receives
`charged_duration * sphincs_hashes / total_hashes` of its job's service;
each privacy witness receives
`charged_duration * leanstark_hashes / total_hashes`. The denominator is
`CircuitCost.hashes(*composition)` using the job's submission composition,
including overhead and nested-proof work, and the charged duration includes
jitter. Witness-backed aggregate inputs affect both prices but contribute
events and service only to `witness_folds`. Their witness identities and
per-leaf prices are frozen at submission, so a later proof upgrade cannot
reprice the observation; an input removed before service starts contributes
no fold events. Overlapping mode 0 claims contribute once per input occurrence,
as charged, rather than being deduplicated into a union.
`service_ms` sums all leaf shares; `repeat_service_ms` sums only repeat shares.
Both total and per-kind `repeat_service_share` divide repeat service by **all
completed proving service** in the run, including internal and leaf-free jobs,
and report `-1` if that denominator is zero. In-flight service is excluded.
Each observer exports this denominator as `completed_service_ms`, including
empty-input completions and active jobs of cancelled or refused plans.
Total and per-kind `unattributed_service_ms` report supported leaf work priced
at submission whose input disappeared before service started. The direct
observer accounts for missing direct inputs; the witness observer also
accounts for witnesses in missing mode 0 inputs. Overhead and recursive-proof
service are not missing leaf work and do not enter that field. These counters
are published only when the job completes, not while it is in flight.

`repeat_service_share` measures global duplication, not service the folding
node could necessarily have reused. The complementary
`covered_at_start_events` and `covered_at_start_service_ms`, in totals and by
kind, count completed raw folds whose leaf was already covered by some
recursive proof in the folding node's held view when that job started.
The proof need not be selected by the job. Mode 0 evidence, structural
coverage outside a proof's claim, and proofs held only by other nodes do not
qualify. The start snapshot survives later arrivals and removals. The
`covered_at_start_events_by_node` and `covered_at_start_service_ms_by_node`
arrays follow node order. This is the population where the node could have
reused something; estimating savings from a first-hop rule requires simulating
that rule, including its timing and dissemination consequences.

Under `deps` or `inputs`, counts and multiplicities remain available, but
every service field, including the denominator and per-node service entries,
is `-1`. Setting `sim.leaf_folds = None` or `sim.witness_folds = None` before a
run independently disables that observer and makes its entire metric `-1`.
Each retains one node bitset per folded leaf and fixed per-kind/node counters.
Outstanding jobs retain submission input identities and mode 0 claim masks;
active jobs also retain one union of the node's held recursive claims. The
union scans that node's held objects once per raw-input job start, without
scanning once per leaf or across all nodes. At 1,000 nodes and 10,000
transactions, the active coverage snapshots need at most 1.25 MB of bit
payload plus Python overhead. Waiting cancellation, queue refusal and
completion release the corresponding job observation. Observations read
frozen claims (or already-cached coverage for claim-less synthetic aggregates),
never traverse recursive provenance or mutate the coverage cache, and change
no node beliefs. A claim-less synthetic aggregate without cached coverage
supplies no interpretable proof claim to these observers.

## Transaction evidence and duplicate burden

**First verified evidence**
: `NetworkSim.transaction_evidence` observes the first admission time for
  each node and transaction, regardless of the carrying object's identity.
  Local birth counts immediately; received evidence counts at admission
  after any verification, not at ingress or queue submission. A completed
  local proof also supplies evidence for its frozen claim. Witness-backed
  mode-zero wrappers count as evidence without implying possession of a
  recursive proof. Wrapper observations use the delivered claim, not its
  aggregate's structural coverage. Re-admission never overwrites a first
  timestamp, and pruning or settlement does not erase it.

  `metrics()["transaction_evidence"]` reports only entry counts: `nodes`,
  `evidence_entries` and `body_entries`. The tables themselves,
  `evidence_ms_by_node` and `body_ms_by_node`, come from
  `sim.transaction_evidence.metrics()` on request; each node's table maps
  leaf index to absolute simulated ms, and a missing entry means no observed
  transition, not a zero time. Beside each table the observer keeps one mask
  per node of the indices already recorded, so a repeated claim costs one
  and-not and a new claim walks only its novel bits; update cost does not
  grow with claims already known. They are kept out of every record because
  they scale with nodes times transactions: on one 900-transaction mainnet
  cell they are 1.9 MB against 0.2 MB for everything else. Storage is
  bounded by two timestamps per node/transaction pair, with no per-admission
  history or retained claim masks. Updates visit the bits in the supplied
  mask and never traverse arena provenance. Direct node-view mutations in
  synthetic fixtures are not observed; a raw synthetic aggregate without a
  frozen claim supplies no interpretable evidence to this observer.

**Body availability**
: The separate first time a transaction body enters the node's available
  body set. A body pending wrapper verification is not yet available.
  Verification completion can make it available; an unresolved reference or
  CPU-refused wrapper can retain it without admitting evidence. A later
  body-bearing copy of an already verified claim can supply a body without
  repeating verification. This measures the model's availability transition,
  not physical ingress, current retention, inclusion, or successful clearance.

**Evidence diffusion**
: `starksim.evidence.diffusion(sim, fraction=0.9, kind="evidence")` returns
  `time_to_fraction_ms`, indexed by transaction leaf index: birth-relative
  delay until at least `ceil(fraction * nodes)` nodes have first admitted
  evidence. `kind="body"` instead queries first body availability. Fractions
  must be finite and in `(0, 1]`. Unreached transactions have delay `-1` and
  remain in `total_txs`; `reached_txs` counts those reaching the threshold.
  `mean_ms` averages reached transactions only and is `-1` for an empty
  reached set. `observed_until_ms` states the observation endpoint. This is
  historical reach, including observations later followed by expiry, not a
  residency or fixed-age inclusion query.

  Setting `sim.transaction_evidence = None` before the run disables this
  observer without changing the trajectory. The metric then returns `-1`,
  as do the diffusion delays, mean and reached count. Placement JSON adds
  `evidence_diffusion` and `body_diffusion`. Its existing `time_to_90_ms`,
  `reached_90`, `measured_txs` and default text retain the legacy
  `bench.placement.diffusion_time` reconstruction of raw object admissions,
  including its readmission counting; they do not acquire evidence meanings.

**Duplicate attribution**
: `starksim.evidence.streamed_duplicate_burden(sim)` reads an accumulator
  updated when `dup` records are written, after trace focus and sampling
  filters. It retains only per-followed-transaction byte totals and message
  totals, O(followed transactions), including followed ids with zero burden.
  `duplicate_burden(sim, trace_text)` remains the equivalent query over saved
  `dup` records from that run. For each record, divide its bytes equally
  among its sorted distinct named followed transaction ids, using integer
  division and assigning remainder bytes to the lowest ids. The rule is
  labelled `equal_integer_shares_among_named_followed_v1`. A wrapper's full
  duplicate bytes enter the attributed total once, not once per transaction.
  This is a bookkeeping allocation among followed transactions, not their
  physical byte composition; changing the followed set changes the shares.

  `by_transaction_bytes` sums to `attributed_duplicate_bytes`, which equals
  `traced_duplicate_bytes` when attribution is available. That sum cannot
  exceed the run's message-level `duplicate_bytes`; the remainder is
  `unattributed_duplicate_bytes`. Message totals include untraced duplicates,
  including block notices. `duplicate_traffic` in `metrics()` records these
  duplicate messages and bytes by payload kind and receiving node,
  independently of tracing. Its totals reconcile with the legacy
  `duplicates` and `duplicate_bytes` counters for simulator deliveries.

  The burden query also includes all-message `ingress_messages`,
  `ingress_bytes` and `ingress_bytes_by_kind`, including wants. Wants are
  not classified as duplicates by the current model; an unanswered want is
  not reclassified as duplicate traffic. The query includes explicit zero
  duplicate-kind entries for such categories. Trace focus and followed ids
  accompany attribution, which is a trace-covered subset, not a global
  estimate. Without a trace header and active followed transactions,
  per-transaction attribution and its remainder report `-1`, while message
  totals remain available. Placement JSON includes this query; its default
  trace follows no transactions, so only the message totals are available.
  Setting `sim.tracer.duplicate_accumulator = None` before the run disables
  streaming attribution without changing the trace; the streamed query then
  returns `-1`. The saved-trace query remains available independently.
  Non-integer duplicate byte sizes from a custom size model cannot support
  integer shares: both burden queries refuse them without interrupting the
  simulation while tracing.

## Plan deadlines and selection decisions

**Plan deadline classes**
: `metrics()["plan_deadlines"]` observes submitted proving plans, not every
  tick or a re-emission of an existing root. The deadline is the decision
  time at `_tick` plus `aggregation_interval`. Per-node `by_node` records
  and their `total` partition `submitted` into `completed_within_interval`
  (including equality), `completed_late`, `terminal_without_completion` and
  `still_outstanding`. Terminal failures partition into `cancelled`,
  `refused_queue` and `refused_over_capacity`; they are known failures even
  before their deadline. A running internal job does not undo cancellation.

  Outstanding plans partition into `outstanding_censored` (observation time
  at or before the deadline) and `outstanding_overdue` (strictly after it).
  Neither is a completed-late plan. These classes do not schedule a timer
  or infer a future completion. `observed_at_ms` and `interval_ms` identify
  the comparison. Unclocked triggers, or `sim.plan_deadlines = None` set
  before the run, make the entire metric `-1`.

  `decision_to_start_ms`, `decision_to_root_ms` and
  `decision_to_emission_ms` are fixed `Moments` histograms with count, sum,
  sum of squares and max, using the recorded `power_minimums`. Each samples
  only plans that reached that milestone; start is the first job's start,
  not every internal proof. Empty populations have count zero and max `-1`.
  Local emission is the wrapper core returned immediately after completion,
  not peer-copy acceptance, egress completion or remote admission. Its
  latency currently equals root latency for that subset. An empty frozen
  claim can produce a root but no wrapper (`completed_without_emission`).
  A completed root need not contain useful live evidence. The observer keeps
  only fixed per-node summaries and outstanding plans; decision timestamps
  are cleared at termination, including when an internal job remains active.

**Selection tiers and budget stops**
: `metrics()["selection_decisions"]` observes calls to `eip_priority`,
  including empty selections and selections later deferred or re-wrapped
  without proving. Its `by_node` and `total` records count `decisions` and
  three `tiers`, in the EIP's order: `previous_root`, `peer_aggregates` by
  marginal live gain, and `fee_leaves` by descending fee, with id breaking
  ties. The EIP's removal step is handled through local discards, not as an
  additional choice tier. The aggregate tier contains all held aggregates
  other than the supplied previous root; it does not filter creator ids.

  Each tier counts selected `objects` and `marginal_live_txs`, the distinct
  live transactions newly added at that point in the selection. Their sum
  over tiers equals the selected live union, not additive claim widths.
  `budget_stops` partitions decisions into `none`, `peer_aggregates`,
  `fee_leaves` or `both`: these record which loops broke on the existing
  budget check. An aggregate stop does not prevent the later leaf loop.
  `none` does not mean the estimate fit: the minimum-fold input allowance
  can exceed the budget without stopping a loop.

**Remaining eligible union**
: `selected_live_union` and `remaining_eligible_union` are per-decision
  `Moments` summaries in `selection_decisions`. The latter counts locally
  eligible live transactions absent from the final selection when a budget
  check stopped a loop, using a union of candidate claims with locally known
  discards removed. It is zero without a budget stop. Overlap with selected
  claims and overlap among remaining claims do not count twice. Eligibility
  means held selection evidence, not body-backed block includability or a
  claim that a different selection could fit those transactions in budget.
  The observation reuses claims read by the selector and direct leaf ids,
  adding no scores, cost evaluations or provenance reads.

  Selectors without this observation, or `sim.selection_decisions = None`
  set before the run, report `-1`, not zero tier activity. For bounded
  diagnostics, replace the supported selector's observer before running
  with `starksim.decision_metrics.SelectionDecisions(node_count,
  sample_limit=limit)`. `limit` is a nonnegative integer, default zero.
  `samples` retains the first such number of decisions across all nodes,
  each with node, tick, tier counts, stop reason and the two union widths.
  This prefix is not a representative sample. No masks or selected-object
  lists are retained. Storage is O(nodes x buckets + sample_limit), apart
  from temporary masks during one selector call. These are observation
  settings and measurements, not variant or trace-header fields.

## Wire copies

**Completed transaction copies**
: `starksim.wire_copies.wire_copies(sim, per_transaction=False)` queries
  cumulative completed egress copies, not acceptance, ingress, verification
  or first reach. `raw_object_bodies` counts full raw leaf objects;
  `wrapper_bodies` counts each bit in the wrapper's `body_mask`;
  `wrapper_claim_mentions` counts each bit in its core's `tx_mask`, including
  hash-substituted transactions and proof-reference copies. The body and
  claim classes overlap: do not add them as independent message counts.
  One wrapper can mention several transactions without becoming several
  messages. Control messages do not count as body copies or claim mentions.

  Counters use raw leaf indices, not object ids or aggregate provenance.
  A completed copy counts even while propagating, waiting for verification,
  rejected on reception or carrying evidence that was already known.
  Refused, queued and partly sent messages contribute no completed copy;
  partial bytes remain visible in traffic samples and recovery state.
  Storage is O(transactions x fixed classes), with no message history.

  The query's `classes` gives each class's count, sum, mean, max, sum of
  squares and fixed power-of-two histogram from `starksim.statistics`.
  `total_txs` is every leaf born by `observed_until_ms`, including those with
  zero copies; means divide by this population. Histogram boundaries are
  recorded as `histogram_power_minimums`, with separate zero, positive
  underflow and overflow buckets. An empty population has zero count/sum
  and undefined mean/max (`-1`). Setting `sim.wire_copies = None` before the
  run disables the observer and makes the query return `-1`; it does not
  report zero copies. `per_transaction=True` adds detached `by_transaction`
  rows keyed by `tx`. Mainnet JSON includes only the summary as `wire_copies`
  at observation end, including the drain. Its optional diffusion queries
  report reach separately, never as a substitute for copies.

**Spanning-tree body-copy reference**
: Reaching all N nodes from one initial body holder over a spanning tree
  needs N-1 successful edge transmissions of that body. Compare this
  arithmetic reference with raw-object plus wrapper body copies, not claim
  mentions. It assumes full reach and omits retransmissions, control traffic,
  proof traffic and multiple entry holders. It is not an "exactly once"
  guarantee for gossip: repeated sends may teach nothing, and low copy
  counts can instead mean that fewer nodes received the transaction.

## CPU work and useful output

**CPU waiting samples and service**
: `metrics()["cpu_accounting"]` reports `total` and node-indexed `by_node`
  records for `verify_recv`, `prove_recursive` and `build_block`, at
  `observed_at_ms`. Each `wait` distribution samples submission-to-start
  time when a job starts, including an immediate start as zero. It contains
  `count`, `sum` in ms, `square_sum` in squared ms, `max`, and a fixed
  `histogram`. With no samples the maximum is `-1`; additive counters are
  zero. Waiting jobs not yet started, cancelled jobs and refused jobs are
  not samples. Zero-cost verification that bypasses the CPU creates no job
  and therefore no waiting sample.

  `service_ms` includes completed service and elapsed service of the active
  job at the current simulation time. Across job kinds it equals CPU busy
  time when every job has a model job kind. This is cumulative service, not
  full durations committed to unfinished jobs, and cannot query history.
  The distributions retain no per-job history; storage is fixed per kind
  and node. The engine's existing completion-only counters are unchanged.

**Power-of-two distribution**
: `starksim.statistics.POWER_MINIMUMS` fixes positive bucket boundaries from
  `2**-20` through `2**40`. The histogram starts with an exact-zero bucket,
  then positive underflow below the first boundary, then half-open buckets
  `[minimum, next_minimum)` and a final unbounded overflow bucket. Boundaries
  appear in `cpu_accounting.power_minimums`; plan ratios use the same
  boundaries, recorded as `plan_work.total.ratio_power_minimums`. Wait
  boundaries are milliseconds; ratio boundaries are dimensionless.

**Plan lifecycle**
: `metrics()["plan_work"]` contains totals and node-indexed `by_node`
  records. `submitted` counts constructed plans before circuit-capacity
  checks or CPU submission, not empty selections, deferred folds or
  re-wrapping. `started` counts a plan once, at its first job's start.
  `completed` counts root production. `cancelled` counts plans whose
  waiting jobs were withdrawn by tick abandonment, or whose root job
  finishes without resolved inputs. `refused` partitions into
  `refused_over_capacity` (circuit limit) and `refused_queue` (CPU limit).
  The first terminal outcome wins, once per plan, even for a many-job tree.

  `submitted = completed + cancelled + refused + still_pending`, per node
  and in total. `still_pending` counts plans still able to complete, queued
  or running. It excludes terminal plans even if a non-preemptible internal
  job remains in service; that job's work still enters CPU service.
  `started` overlaps terminal counts and does not enter the partition.

**Selection estimate and charged plan cost**
: `plan_work` accumulates `completed_estimated_ms` from each completed
  plan's original `Selection.estimated_cost`, and `completed_charged_ms`
  from the durations execution actually charged to all its jobs, including
  internal proofs and cost jitter. It does not re-evaluate cost callables or
  draw jitter to observe an estimate. `charged_estimate_ratio` contains
  bounded moments and a histogram of each completed plan's charged total
  divided by its selection estimate. A zero estimate makes that ratio
  unavailable, including zero divided by zero; `ratio_unavailable` counts
  these plans. Its histogram count plus `ratio_unavailable` equals
  `completed`. Empty ratio samples have maximum `-1`, not zero.

  `plan_work.refused_estimated_ms` sums the original selection estimates
  for refused plans, once per plan. It is not an estimate of every internal
  job that an abandoned plan would have submitted. Each CPU-kind record's
  `refused_jobs` and `refused_estimated_ms` instead count queue-refused jobs
  and sum their already assigned durations, including any jitter. The
  top-level `cpu_refused_estimated_ms` is the receive-verification subtotal,
  beside the existing `cpu_refused_*` message counts. Circuit-refused plans
  never reach a CPU queue, so their estimates appear only in `plan_work`.

**Useful root stages**
: `plan_work.proofs` counts all internally produced proof objects, including
  roots; `internal_nonroot_proofs` excludes roots. `roots_completed` counts
  completed plan roots. `roots_emitted` counts those identities once a
  wrapper naming the root has completely left a node's egress server, not
  when its core is created or its send is accepted. Multiple peers and
  repeated wrappers do not multiply the count; wrapper mode and proof-by-ref
  do not change this transport definition.

  `roots_contributed` counts completed roots selected directly into a block
  with positive ordered `marginal_included_txs`. `roots_emitted_contributed`
  is the intersection of emitted and contributing roots by observation
  time. These obey `proofs >= roots_completed >= roots_emitted >=
  roots_emitted_contributed`, per creator node and in total. Local inclusion
  can precede emission; the intersection does not assert a causal order.
  `roots_contributed` preserves that locally useful output separately.
  Contribution does not credit ancestors, overlapping claims with no new
  marginal inclusion, expired transactions, or objects injected outside the
  observed plan lifecycle. Deduplication retains one small identity record
  per completed root, not per send or per block.

## Fixed-age outcomes and utilisation windows

**Birth-indexed outcome times**
: `metrics()["transaction_outcomes"]` contains one detached record per
  transaction born through `NetworkSim`: `tx` is the leaf index, not the
  object id; `born_ms`, `included_ms` and `expired_ms` are absolute simulated
  times. The latter two are `-1` until the respective transition is observed.
  `outcomes_observed_until_ms` identifies the observation endpoint. Inclusion
  is canonical block settlement, not a receiver's notification time. Expiry
  records actual exclusion, not a scheduled deadline that inclusion may have
  pre-empted. It is not successful clearance. These records survive pruning
  and folding; they require linear storage in the number of transactions.

**Fixed-age outcome**
: `starksim.outcomes.outcomes_at_age(sim, age_ms, birth_start_ms,
  birth_end_ms)` classifies births in the half-open interval
  `[birth_start_ms, birth_end_ms)` at each birth's own `born_ms + age_ms`.
  Times and ages are finite, nonnegative milliseconds; the birth interval
  must have positive width. At an inclusive age boundary a transaction is
  included, expired, or still pending. Settlement after that boundary leaves
  it pending at the requested age, even if it has settled by the time of the
  query. Expiry and inclusion are mutually exclusive.

  The result carries the requested age, birth bounds and `observed_until_ms`.
  `born_txs = mature_txs + censored_txs`: a censored birth has not yet reached
  the requested age and enters none of the outcome counts, even if already
  included or expired. `mature_txs` partitions into `included_txs`,
  `expired_txs`, `pending_txs` and `unknown_txs`. The last catches settlement
  injected directly into `ChainState` without an observed timestamp; it is
  not pending. The three corresponding `*_fraction` fields divide by mature
  births, or return `-1` when no birth is mature or any outcome is unknown.
  Counts of empty observed sets remain zero; an undefined fraction does not.

**Equal-age cohort comparison**
: `starksim.outcomes.compare_cohorts(sim, age_ms, early, late)` accepts two
  `(birth_start_ms, birth_end_ms)` pairs and returns both summaries.
  `included_fraction_late_minus_early` is their difference only when each
  birth interval has closed (`cohort_closed`), both populations are nonempty,
  and every birth is mature with a known outcome. Otherwise it is `-1` and
  `comparable` is false. Check that flag: a measured difference can also be
  minus one. A fraction among mature births of an incomplete cohort is not a
  complete-cohort comparison. Querying another age does not advance the run.

  In `bench.mainnet` JSON, `fixed_age_outcomes` holds one such comparison per
  requested age, plus an `all` summary for the full birth window. It is `-1`
  when no ages were requested. The early/late split and flag are documented
  in [configuration.md](configuration.md#what-the-harnesses-expose).

**Boundary-sampled utilisation window**
: `starksim.outcomes.BusySample(sim, boundary_ms)` reads each CPU and egress
  server's cumulative busy time, including active service, only when
  `boundary_ms == sim.engine.now`. Retain a sample at each endpoint;
  `utilisation_window(start, end)` subtracts them and divides by elapsed ms.
  Samples must belong to the same run and have increasing times. Historical
  or future counter reads are refused, not interpolated.

  The result carries `start_ms`, `end_ms`, and, for both `cpu` and `egress`,
  `*_busy_ms_by_node`, `*_utilisation_by_node` and mean `*_utilisation`.
  Total window busy time equals mean utilisation times duration times node
  count; the mean is `-1` for an empty server population. Retained samples
  stay usable after a longer drain without rereading counters. Mainnet's
  `load_utilisation` uses the birth-window endpoints for both resources;
  legacy whole-run utilisation retains its observation-end denominator.

## Boundary-sampled traffic

**Traffic sample and window**
: `starksim.outcomes.TrafficSample(sim, boundary_ms)` copies cumulative
  per-node bytes at `engine.now`, refusing historical, future or incomplete
  boundaries (events at that instant must all have run). The stages are
  `attempted`, `refused` at egress, `accepted` (offered), `completed` (sent),
  and partial-inclusive `wire`. Payload kinds are `object`, `wrapper`,
  `announce`, `want` and `block`; these are whole-message classes, not byte
  components. CPU refusals do not enter sender-side refusal accounting.

  `traffic_window(start, end)` requires samples from the same run with
  strictly increasing times. Its `*_bytes_per_second_by_node_kind` lists
  contain each node's rate per payload kind; `*_bytes_per_second_by_kind`
  contains the mean of those rates over all nodes, including idle nodes.
  `*_bytes_per_second_by_node` sums kinds per node; `*_bytes_per_second` is
  its network mean, not the network sum. Elapsed milliseconds are converted
  to seconds. Sum the wire kind rates and multiply by elapsed seconds to
  recover the partial-inclusive wire byte delta, up to floating-point
  rounding. An idle interval is zero; an unavailable classification is `-1`
  and makes the corresponding mean unavailable. Directly injected egress
  jobs without payload accounting can leave wire totals available but their
  kind split unavailable.

  Sampling and retained storage are O(nodes x fixed categories), with no
  queue scan or event history. Samples remain usable after a longer drain.
  Mainnet JSON's `load_traffic` uses zero before birth scheduling and the
  completed birth-window endpoint, so time-zero sends are included. Other
  complete endpoint pairs measure counter differences over `(start, end]`.

**Completed wrapper encodings**
: `wrapper_copies_by_node_encoding` counts recipient copies whose last byte
  left egress within the window; `wrapper_copies_by_encoding` sums nodes.
  `full_proof` carries a mode-1 recursive proof, `proof_reference` carries
  its hash, and `mode_zero_witnesses` carries raw witnesses instead of a
  recursive proof. A reference flag takes precedence over mode zero, matching
  the bytes selected by the size model. These counts partition completed
  copies, not unique proofs, arrivals or `_emit_wrapper` cores. Suppressed,
  refused, waiting and partly sent copies contribute none. Setting
  `sim.wrapper_copies = None` before a run disables this counter; its
  classifications then report `-1`, not zero copies.

**Reference cadence rate**
: `reference_cadence_bytes_per_second_by_node` is each node's actual peer
  count times `size_recursive_proof`, divided by `aggregation_interval` in
  seconds. `reference_cadence_bytes_per_second` is its mean over all nodes.
  Both report `-1` for an unclocked trigger. This is the arithmetic proof
  component of one full wrapper per peer per tick, not a measured rate or a
  bound on total traffic: it excludes bodies, hashes, triples and control
  messages, and does not assume proving completes on cadence.

## Recovery measurements

**Active and waiting work**
: `starksim.recovery.recovery_state(sim)`, `metrics()` and overload's load/end
  snapshots carry `cpu_work` and `egress_work`, with totals and `by_node`
  records. Each distinguishes `active_jobs`, `waiting_jobs`,
  `active_remaining_ms` and `waiting_ms`: remaining service on the active
  job versus the full charged service of waiting jobs. Egress also records
  `active_remaining_bytes` and `waiting_bytes`. These read actual payload
  sizes, including on unbounded queues. Unsized synthetic egress jobs make
  the affected byte measurement `-1`, not zero.

  `link_work` is a list of the same message-work records, identified by
  `sender` and `receiver`, for optional per-link servers. Records describe
  current work at `recovery_observed_at_ms`, not cumulative queue maxima.
  Observation scans current queues only when requested; it does not install
  an event-loop observer.

**Pending delivery**
: A message past node egress but not yet at receiver ingress. The
  `pending_deliveries` count and `pending_delivery_bytes` include both
  optional link serialization and propagation latency; link work is a subset,
  not additional messages. Bytes here are complete payload bytes awaiting
  delivery, not remaining serialization bytes. Distinct copies count
  separately even when their payloads are identical. For sends through the
  wiring, accepted messages partition into delivered messages, pending
  deliveries, and active/waiting egress jobs. Egress-refused attempts never
  enter that partition. Tracking storage is bounded by outstanding deliveries.

**Unresolved request**
: A node/object entry in `NodeView.requested`, counted by
  `unresolved_requests` and `unresolved_requests_by_node`. It remains until
  admission, abandonment or timeout clears it; a response awaiting CPU
  verification can still be unresolved. Source-history entries, cumulative
  `unserved_wants`, and timeout events left after successful admission are
  not outstanding requests.

**Egress waiting queue empty**
: `egress_waiting_empty` means every node-egress waiting queue contains no
  job, independently of active transmission. Overload's legacy `drained`
  retains its test of end-snapshot `queue_bytes == 0` and its existing label.
  That legacy field sums server queue weights: bytes on bounded egress,
  job counts on unbounded egress. Neither test establishes quiescence.

**Transport idle and transport quiescent**
: `transport_idle` means no active or waiting CPU, egress or link job and no
  pending delivery. `transport_quiescent` additionally requires no unresolved
  request. Thus an idle transport can remain blocked on an unanswered want.
  These are instantaneous states: future births, ticks and timeout events
  are not active work, and periodic traffic can resume after a quiet instant.

**Useful service recovered**
: `useful_service_recovered` is `-1` in an instantaneous snapshot. A quiet
  transport cannot establish a return to a pre-burst inclusion/work envelope.
  Overload's current wire-only fixture has neither block building nor a
  pre-burst service reference, so it also reports `-1`, even when `drained`
  or `transport_quiescent` is true.

  `RecoverySample(sim, boundary_ms)` captures a detached state only when the
  simulation has reached that exact boundary. The function
  `assess_useful_recovery(reference, recovery, confirmation, *,
  rate_tolerance=0.0, work_tolerance=0.0)` accepts three sequences of those
  samples from one run. Each window needs at least two samples, equal
  duration and an identical offset grid; the reference precedes recovery,
  and confirmation immediately follows recovery. Missing windows or a
  reference without positive inclusion return `-1`.

  The reference's mean inclusion rate sets a relative band using
  `rate_tolerance`, which must be less than one. Both subsequent window
  rates must lie inside it. At every sampled boundary, pending transaction
  count, CPU jobs and remaining ms, egress jobs and remaining bytes, pending
  delivery count/bytes and unresolved requests must stay below their
  reference sampled maxima multiplied by `1 + work_tolerance`. Nonnegative,
  finite tolerances must be chosen before inspecting recovery, and are
  returned with the bounds, window rates and sampled work peaks. Unknown
  work measurements make the assessment `-1`. Otherwise the assessment
  returns true or false in its `useful_service_recovered` field and can be
  attached to a bench's JSON metrics with `emit_json_record`.

  Expiry is recorded separately in each window and cannot supply inclusion
  rate. Periodic work need not cease if useful inclusion and pending work
  return to the reference envelope. Peaks are sampled, not continuous-time
  maxima: intervals between samples and stability after confirmation remain
  outside this assessment. Retained samples do not change as the drain grows.

## Workload manifests and arrival profiles

**Workload manifest**
: An immutable ordered tuple of `bench.workload.Birth` records: `time_ms`,
  `kind`, `fee`, `lifetime_ms` and `node_id`. Times are absolute simulated
  milliseconds; lifetime is a duration or `null` for no expiry. Entry nodes
  are explicit, not a request to draw a node during replay. Equal-time births
  retain record order. Scheduling consumes the records without retiming,
  rebalancing or new random choices, and does not consume `sim.rng_entry`.

  The serialized format carries `manifest_version` and the profile, birth
  window, period, phase and eligible entry pool. `generation_window_ms` and
  `generation_count` retain the source fixture's extent even for a prefix.
  Unsupported versions and a declared mixture inconsistent with the records
  are refused. Storage and serialization are linear in the birth count.

**Realised mixture**
: `realised_mix` counts actual `q` and `p` births in the manifest, including
  zero for an absent kind. It is not the requested privacy-share parameter.
  A prefix keeps births strictly before its cutoff, without shifting their
  times or replacing their attributes, and reports its own mixture. Generating
  a new shorter workload is not equivalent: the legacy fixed-count shuffle
  can change earlier kinds, and conditioned arrival times depend on the
  generation window. Obtain paired duration fixtures by prefixing one longer
  manifest, not by independently regenerating shorter ones.

**Arrival profile**
: `uniform` uses the legacy spacing `window / count`, starting at zero.
  Mainnet retains its existing `mixed_kinds` shuffle and fee cycle. Entry
  assignments reproduce the legacy `"{seed}:entry"` draws into the eligible
  pool before replay. All profiles assign those same attributes to successive
  chronological births at equal seed, count and entry pool.

  `poisson` sorts independent uniform times in the birth window conditional
  on the fixed transaction count. It is not an unconditioned Poisson process:
  counts do not fluctuate between seeds. Timing uses the separate stream
  `"{seed}:arrivals:poisson:v1"`.

  `bursty` has one active fifth of each slot-length period and no births in
  its other four fifths. Times are independent uniform draws over the active
  intervals, conditioned on the total count, then sorted. Phase uses
  `"{seed}:arrivals:bursty-phase:v1"`, independently of count; timing uses
  `"{seed}:arrivals:bursty:v1"`. Generation requires whole periods, so active
  time is one fifth of the full window and expected rate there is five times
  the base rate `count / window`. Individual periods have fluctuating counts.
  Prefixes may end within a period and retain the parent's phase and records;
  their realised count need not equal the base rate times prefix duration.

  These labels and the manifest version identify new workload fixtures, not
  model policies. Defaults preserve uniform simulation results and text;
  JSON gains the workload metadata listed in
  [configuration.md](configuration.md#what-the-harnesses-expose).

## Run identity

**Identity block**
: Every benchmark JSON envelope emitted by `bench.common.emit_json_record`
  includes `identity`, derived from the constructed simulation and its
  workload. `starksim.identity.run_identity(sim, workload, benchmark)` is a
  read-only query: it neither evaluates cost callables nor draws from random
  streams, schedules events, or writes trace records. Identity complements
  the variant and workload; it is not a claim that two differing workloads
  or policies are equivalent.

**Topology fingerprint**
: A versioned SHA-256 digest of node-indexed adjacency rows and per-edge
  latencies, captured at simulation construction. Neighbours are sorted;
  each directed entry carries the latency as an exact hexadecimal float.
  Node count and empty rows retain isolated nodes. The digest is streamed
  rather than retaining another graph. A private copy of `UniformLatency`
  resolves uncached values without warming the run's cache. The same graph
  and latencies have the same digest regardless of construction seed or
  cache state; a different seed matters only if it changes those inputs.
  Arbitrary latency implementations are not executed for identification and
  report `-1`. The fingerprint describes construction, not later external
  mutations to the overlay.

**Workload generator version**
: Manifest replay records its integer manifest version on the simulation;
  an inconsistent workload version is refused. Non-manifest benchmark JSON
  uses `legacy:<benchmark>:v1`, labelling the existing generator without
  claiming it is prefix-stable or manifest-backed. A direct simulation
  without generator metadata reports `-1` in its metrics. Sweep records its
  legacy generator label before running so its CSV and JSON agree. In JSON,
  the scalar identity fields in metrics are resolved from the same identity
  block, including when only the envelope knows the legacy harness name.

**Cost profile**
: Identity records the execution `CostTable`, its selection-basis copy,
  `jitter_frac`, original cost seed, effective execution basis and any
  `NativeVerifyCost`. Constants retain numeric milliseconds; `LinearCost`
  retains numeric ms per unit; every `CircuitCost` field and all native
  verification coefficients are included numerically. The stored cost seed
  need not equal the simulation seed; it is not inferred from it. Labelled
  string seeds, if supplied, retain their literal value. The native profile
  is `-1` when absent; its presence does not remove dormant receive-cost
  settings from the table record.

  `cost_table_profile(table)` and `restore_cost_table(profile)` round-trip
  supported construction parameters, including the initial jitter stream,
  not the current RNG state or a running simulation. Unsupported custom
  callables have `type="unavailable"`, parameters `-1`, and make the cost
  profile's `complete` flag false. They are not approximated by probes or
  repr strings. The benchmark generators use constants, structured linear
  costs or circuit costs so their built-in profiles are complete.

**Node assignment and code version**
: Identity includes the resolved entry-node list, the dedicated builder id
  (`-1` when absent), and the constructed proposer schedule's mode, starting
  id, node count, seed and rule version. These describe the existing
  `ProposerSchedule` rule without drawing a replacement schedule. The trace
  schema version accompanies them. `simulator_code_version` is the commit
  readable from Git metadata when the identity is queried, including
  detached HEAD and linked worktrees; absent or unsupported metadata reports
  `-1`. No git process is invoked. A commit id is not a dirty-worktree check
  and does not identify uncommitted edits or certify the loaded source.

  CSV appends only `topology_fingerprint` and `workload_generator_version`,
  in that order: both are stable for a given run. The commit id is JSON-only
  on purpose, since a CSV row carrying it would change with every commit and
  a baseline could never reproduce byte-for-byte. The older columns and their
  meanings are unchanged; full cost and assignment records remain JSON-only.
  These fields do not enter the trace header or change its schema.

## Objective labels

Defined in [model.md](model.md); [results.md](results.md) is organised by them.

**O1 — bandwidth**
: What offered load fits an ordinary node uplink, and whether a mainnet-shaped
  load falls inside it.

**O2 — compute**
: What proving rate keeping pace with the emission cadence requires, and whether
  an ordinary machine sustains it. Coupled to O1 through the cadence, not
  independent of it.

**O3 — aggregation leverage**
: How many transactions one selected object claims, and therefore what a block
  settles for a given fold budget.

**O4 — limits**
: Where capacity is, what breaks first past it, and whether the network recovers
  once load stops.

**O5 — protocol choices**
: Which of the EIP's open choices — relay, wrapper economy, supersession, fold
  plan, CPU discipline, inclusion requirement, selection, eviction — change cost
  or inclusion, and in which direction.

**O6 — position**
: Whether a transaction's entry point or the proposer's location decides
  inclusion and delay.

## Policy names

**`flood`**
: Push each full raw object to peers, then verify, admit, and forward it.

**`announce_fetch`**
: Announce an object identity; interested peers request and receive the full
  object before the usual verification and relay path.

**`wrapper_only`**
: Never relay raw objects. Claims and bodies move through periodic wrappers and
  verified re-aggregation.

**`full`**
: Send the complete wrapper proof to every peer on every emission.

**`skip_unchanged`**
: Suppress a peer copy when that peer is known to retain the exact proof and
  there are no new bodies for it.

**`proof_by_ref`**
: Replace the recursive proof with its hash when the peer is known to retain
  that proof.

**`lean`**
: Combine `skip_unchanged` and `proof_by_ref`.

## Trace event kinds

**`cfg`**
: First record: variant identity, schema version, focus, sampling, counter grid,
  follow selection, and optional topology.

**`arr`**
: A leaf transaction was created and admitted at its entry node.

**`snd`**
: A payload was accepted by a sender's egress queue. This is the only sampled
  event kind.

**`drp`**
: One message was refused by an egress or CPU queue. It records a loss, not
  traffic successfully sent.

**`adm`**
: A node admitted an object after delivery and any required verification.

**`drop`**
: One or more objects left a node's view through supersession, pruning, or
  eviction; unlike `drp`, it is a residency change rather than a message loss.

**`knw`**
: A followed transaction changed knowledge class at a node after a complete
  event boundary.

**`wrp`**
: A node emitted one wrapper core around a root proof.

**`blk`**
: A slot proposer built a block, including counts for selected objects,
  included transactions, and stale attempted coverage.

**`cnt`**
: Exact cumulative per-node counters and queue gauges on the observation grid.

**`seg`**
: One `NetworkSim.run` call ended, with its observed interval and stop reason.

**`end`**
: The trace was explicitly sealed as complete, cancelled, or errored.

The seven knowledge classes used by `knw`, in precedence order, are `settled`,
`held_and_body`, `held_no_body`, `body_only`, `verifying`, `requested`, and
`none`. See [trace-format.md](trace-format.md) for field-level definitions.
