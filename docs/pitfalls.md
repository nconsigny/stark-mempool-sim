# Pitfalls

Things that look fine and are not: the rule, and why it holds. A mistake is
named only where the shape of it is the warning. Terms are defined in
[glossary.md](glossary.md).

## Model concepts that are easy to conflate

**`coverage` is not `claim`.** Coverage is what a fold structurally contains;
a claim is what its proof asserts, frozen when the proving job started. Anything
deciding usefulness, selection or inclusion must call `Arena.claim`. Reading
coverage instead lets a receiver rely on a dependency the prover deliberately
dropped.

**`residency` is not `reach`.** `residency_fraction` is who holds an object
*now*; reach is who ever admitted it. The measurement API reads current
holdings, so `admission_delays` and its two descendants **refuse** for an object
that has departed every view, or for a window the run has not observed.
Reconstruct reach from the `arr`, `adm` and `drop` trace records instead.

**`offered_bytes` is neither attempted demand nor wire traffic.** Offered
counts bytes accepted by egress; `attempted_bytes` includes egress-refused
sends, and `wire_bytes` counts what has crossed, including partial sends.
Accepted work still waiting or transmitting makes offered exceed wire bytes.
To reconstruct attempts, add `egress_refused_bytes`, not `dropped_bytes`:
the latter also includes CPU-refused messages whose bytes already arrived.
Even attempted demand depends on relay and retry behavior, so keep the
exogenous transaction birth rate beside it; see [glossary.md](glossary.md).

**`seen` is a forgetful window.** It may have dropped an identity the node still
holds, so duplicate checks consult `held` and `verifying` directly rather than
trusting `seen` alone.

## Measurement and instrumentation

**A statistic can measure its own procedure.** Before quoting a number, ask what
could change it that is *not* the thing it claims to measure — then vary that and
assert it does not move. Both measurement harnesses here once reported values
that tracked how the measurement was set up rather than the network, and no test
caught either, because each computed exactly what it claimed.
`tests/test_measurement.py` pins this down.

**`adm` is not transaction evidence.** It names a carrying object, so matching
it only to raw birth object ids misses transactions admitted inside wrapper
claims. Repeated object admissions can also count the same node twice. Keep
placement's legacy raw-admission statistic for its published comparisons;
use [first-evidence diffusion](glossary.md#transaction-evidence-and-duplicate-burden)
for transaction reach across relay policies. Body availability is separate,
and neither first timestamp establishes that both are held now. Duplicate
wrappers also name multiple transactions: assigning their full bytes to each
one multiplies traffic. Use the declared shared-byte attribution and retain
message-level totals, including categories absent from followed traces.

**Same rate, new fixture.** Uniform, fixed-count Poisson-conditioned and bursty
arrivals can have the same average transaction rate and different congestion.
Keep profile/version and realised mixture with the result. Hold birth
attributes fixed when comparing profiles; for duration comparisons, take
prefixes of a longer manifest instead of rerunning the shorter generator.
The [manifest contract](glossary.md#workload-manifests-and-arrival-profiles)
defines what those comparisons preserve. Previously published uniform results
are not measurements of the other profiles.

**Fewer copies by reaching fewer nodes is not an economy.** Compare
[wire-copy counts](glossary.md#wire-copies) under the same birth manifest
and observation horizon, beside evidence/body diffusion and inclusion.
Refused or partly sent messages contribute no completed copy; low counts
can therefore indicate a stalled uplink or incomplete spread. First reach
counts distinct nodes and cannot reconstruct repeated transmissions to
them. Wrapper claim mentions are not additional body copies or independent
messages, and duplicate-byte shares are attribution, not copy counts.

**Terminal DAG roots are not completed plan roots.** Later consumption can
remove a terminal object before its successor completes; an unfinished plan
can leave an unused internal output terminal. Keep `bench.mainnet`'s legacy
`root_claim` paired with `root_count`, and use `plan_roots_completed` for
completion-time means. A claim-weighted mean is not overlap-free: use a union
for distinct transactions and ordered marginal contributions for block
density. Definitions are in
[glossary.md](glossary.md#aggregation-measurements).

**A block changes the view being measured.** Its candidate union must be
observed before settlement, from the proposer's held objects rather than the
whole arena. A full object allowance alone does not show a binding budget:
additional body-backed live claims must remain locally available. That is
still a statement about the current selection, not an optimal builder. The
`block_observations` records preserve this distinction without changing the
selector's scoring or reading canonical settlement into its decisions.

**Observation is a cost.** An instrument that dominates its subject is broken
even when it reports the truth: the knowledge tracer was once quadratic while
the simulation was linear. Instrument event counts rather than wall-clock time,
and bound the instrument. See
[ADR 0006](decisions/0006-observation-cost.md).

**`busy_time_at(t)` requires `t == now`.** It is a counter sampled as the clock
passes, not a query over history. Earlier and later readings are refused
deliberately rather than interpolated.

**Egress busy time is not wrapper bytes.** Bodies, triples and control
messages share the uplink. Multiplying busy time by capacity gives total
wire bytes, not recursive-proof bytes; completed-message counters also omit
the active message's transmitted portion. Use the
[traffic window](glossary.md#boundary-sampled-traffic) for per-kind wire
rates and completed wrapper encodings. A wrapper-kind rate still includes
its bodies, hashes and triples. Compare the reference cadence proof rate
with that distinction intact, alongside refusals and unfinished work.

**End-of-drain inclusion gives late births less time.** Compare birth cohorts
at the same age, exclude immature births even when they have already settled,
and keep expiry separate from inclusion. A complete-cohort contrast must also
wait for the cohort's birth interval to close. Freeze utilisation endpoints
independently of the drain: a longer idle tail is not reduced service demand.
The [fixed-age queries and boundary samples](glossary.md#fixed-age-outcomes-and-utilisation-windows)
preserve these distinctions; legacy terminal inclusion and included-only
delay do not acquire fixed-age meanings.

**A structural zero is not a measurement.** `stale_deps_proved` reports `-1`
under `chain_notify="instant"`, because the effect it measures cannot exist in
that configuration. `-1` and `-1.0` are the sentinel for "this run cannot answer
this", and any new metric with the same property must use it rather than
reporting a zero that reads as evidence.

**An empty waiting queue is not recovery.** Its last message may still be in
service or propagating, and an idle transport may still owe a request that
will never be answered. Useful service requires inclusion and pending work
to return to a pre-burst reference and stay there through a confirmation
window; expiry cannot stand in for inclusion. The
[recovery measurements](glossary.md#recovery-measurements) keep these states
separate. Their sampled work peaks do not bound excursions between samples.

**A short CPU queue can be produced by refusal.** Compare waiting samples
with refused job counts and estimated milliseconds, not only service time.
Waiting distributions sample jobs that started; a job still waiting has a
censored wait and contributes no sample, even if it will wait longest. Plan
cost ratios condition on completion and exclude failed or unfinished plans;
keep lifecycle counts beside them. A cancelled plan may still spend CPU on
an internal job, and a completed root may never leave egress. The
[CPU work and useful output counters](glossary.md#cpu-work-and-useful-output)
separate these populations. Emitted-and-contributing roots establish an
intersection, not that transmission caused inclusion or that ancestor work
was unnecessary.

**A charged/estimate ratio of one can coexist with a large queue.** The
ratio compares completed plans' service costs, not their waiting time or
decision-to-completion latency. Read it beside
[plan deadlines and selection decisions](glossary.md#plan-deadlines-and-selection-decisions):
unfinished plans have censored or overdue deadlines, terminal failures are
not completions, and a budget-limited selection can leave a locally eligible
union behind even when its eventual charge matches its estimate. Local
wrapper emission also says nothing about how soon a peer's copy leaves
egress or becomes verified evidence.

**`variant()` reaches the trace header; `metrics()` does not.** Configuration
belongs in the variant, measurements in the metrics. A *bound* placed among the
measurements is invisible to the viewer, and a panel drawing fullness against
that bound will silently draw the unbounded case.

**Wall-clock timings on a shared machine are not signal.** Check the machine's
load before diagnosing a slowdown in the simulator.

## Configuration combinations that disable what you are measuring

**The default proving budget is one aggregation interval, and a cost function
can exhaust it at one input.** Selection takes a first input and then stops as
soon as the estimate exceeds the budget, so any cost whose value already exceeds
`aggregation_interval` at one input makes every claim one object wide —
aggregation is off while the configuration appears to be pricing it. This is
basis-independent: a *constant* `prove_recursive` of 1500 ms against the default
1000 ms interval does it under `deps`, with no per-input arithmetic involved.
The budget may not refuse a node its first *fold*, so two inputs are always
taken, but everything past that binds. `proving_budget=None` is not unbounded;
pass a large number when you want that. The tell is the mean fold width, and it
is worth checking before any explanation of a cost sweep.

**A job's price is fixed when it is queued; its claim is frozen when it starts.**
Those are different instants, and knowledge moves between them. The price is
taken from what the job would assert given what the node knows at *submission*
-- not from the plan, which may be older still -- while the claim it actually
asserts is frozen when the CPU picks it up. A job that waits while a block
settles its dependencies is therefore charged for more than it proves, and that
residue is the stale-work window the model already counts through
`stale_deps_proved`. Closing it entirely would mean pricing a job at start,
which a queue cannot do: the duration is what schedules the completion.

**A selection estimate must be in the unit execution charges.** Selection
prices a candidate set with `estimate_cost`; execution prices the root job. If
the two use different units, a binding proving budget refuses selections for a
cost nothing ever pays, and the run reports a *budget ceiling* while appearing
to report a cost. This shipped twice: a per-dependency estimate against a
per-input charge under `prove_cost_basis="inputs"`, which reduced selections to
one dependency at the default budget; and under `"circuit"`, a bare count read
as that many nested proofs, which overstated three signatures by 21× and capped
every claim at two dependencies. Each produced a published result whose stated
mechanism was wrong. Any new basis must pass execution's own composition
function to the estimate, and a cost sweep that changes an outcome should be
checked against the mean fold width before the explanation is believed.

**Evidence has a class, and identity is not evidence.** Recording that a node
holds proof `P` is a claim about bytes it received. A mode 0 wrapper carries
witnesses and no proof, so installing `P` from one lets the node be answered
with `P`'s 32-byte hash — a reference to bytes that never crossed the wire —
and prices a later fold over it as one nested verification rather than every
signature behind it. Both errors flatter the cheaper mode. When a payload can
carry alternative evidence for the same identity, the receiver must record
*which*.

**A callable cost is identified by probes, and probes can be blind.** The
variant samples a callable `verify_recv` or `prove_recursive` at sizes 1 and
100, so two callables that agree at both probes and differ elsewhere produce
identical variants for materially different runs. Constants and the
structured cost objects (`CircuitCost`, `NativeVerifyCost`) are identified
field by field; an exotic callable is on the caller to attribute.

**A variant that omits a parameter cannot attribute its own outcome.** Sampling
a cost at one and a hundred inputs identifies a scalar, not a `CircuitCost`: the
per-kind weights and the trace cap were invisible, so a run that proved happily
and a run that refused every job as impossible produced byte-identical variants,
CSV rows and trace headers. Anything that can change an outcome belongs in
`variant()`, whatever else in the row appears to cover it.

**A transition in the same event as a delivery was not necessarily caused by
it.** One admission can evict another object and change two transactions'
classes at once; the delivering peer belongs only to the transition whose new
evidence is the delivered object. `KnowledgeTracer.flush` applies that rule,
and every delivery path has to pass the peer and the object to `mark` — a
path that forgets produces no error, only a spread tree with an edge missing.

**Refuse impossible work before charging for it.** A server with an idle queue
begins service *synchronously* on submission, and `cancel_waiting` cannot recall
work in progress. So a check applied per job inside a submission loop cannot
refuse a plan: earlier jobs have already started. Anything decidable for the
whole plan — the trace-length ceiling is, being a pure function of the plan —
must be decided before the first submission.

## Simulation semantics

**With spread ticks, no particular node is guaranteed to prove.** A peer's
aggregate may arrive first, and re-emitting it is correct behaviour rather than a
missed fold. A test that needs one specific node to prove must pass
`aggregation_trigger="aligned"`.

**Tick and slot numbering continue across scheduling calls.** Generation-based
cancellation compares tick numbers, so restarting numbering would make stale
work look current.

## Tracing and the viewer

**Trace line order is load-bearing.** A `drop` emitted before the `adm` it
follows leaves the object resident in any reducer that replays in order.

**A tracer node id is a stream identity, not an attribute.** Passing something
merely *about* a node as that id silently opts the event into focus filtering,
which can remove most of a focused trace.

**A step function's gaps are data, not absence.** Residency changes only when a
node admits or drops, so between samples it holds its value. Treating the gaps
as missing leaves a heatmap looking broken while the numbers behind it are
exact. Carry values forward — but only as far as the object was actually
observed, or the display invents coverage past the horizon.

**An author `display` beats `[hidden]`.** Setting `.hidden` on an element whose
CSS gives it a `display` does nothing; it needs an explicit
`#thing[hidden] { display: none }`. `tools/check_viewer.mjs` checks this
statically, because no reducer test can see it.
