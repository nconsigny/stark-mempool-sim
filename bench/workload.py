"""Versioned birth fixtures shared by benchmark harnesses."""

from bisect import bisect_right
from dataclasses import asdict, dataclass
import math
import random

from starksim.arena import KIND_PQ, KIND_PRIVACY


MANIFEST_VERSION = 1
ARRIVAL_PROFILES = ("uniform", "poisson", "bursty")


@dataclass(frozen=True, slots=True)
class Birth:
    time_ms: float
    kind: str
    fee: float
    lifetime_ms: float | None
    node_id: int

    def __post_init__(self):
        if not math.isfinite(self.time_ms) or self.time_ms < 0:
            raise ValueError("birth time must be finite and nonnegative")
        if self.kind not in (KIND_PQ, KIND_PRIVACY):
            raise ValueError("unknown transaction kind")
        if not math.isfinite(self.fee):
            raise ValueError("fee must be finite")
        if self.lifetime_ms is not None:
            if not math.isfinite(self.lifetime_ms) or self.lifetime_ms < 0:
                raise ValueError("lifetime must be finite and nonnegative")
        if type(self.node_id) is not int or self.node_id < 0:
            raise ValueError("entry node must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class WorkloadManifest:
    births: tuple
    arrivals: str
    window_ms: float
    period_ms: float
    phase_ms: float | None
    entry_nodes: tuple
    generation_window_ms: float
    generation_count: int
    version: int = MANIFEST_VERSION

    def __post_init__(self):
        if type(self.version) is not int or self.version != MANIFEST_VERSION:
            raise ValueError("unsupported workload manifest version")
        if self.arrivals not in ARRIVAL_PROFILES:
            raise ValueError("unknown arrival profile")
        for duration in (self.window_ms, self.period_ms,
                         self.generation_window_ms):
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("manifest durations must be finite and positive")
        if self.window_ms > self.generation_window_ms:
            raise ValueError("prefix exceeds its generation window")
        object.__setattr__(self, "births", tuple(self.births))
        object.__setattr__(self, "entry_nodes", tuple(self.entry_nodes))
        if (type(self.generation_count) is not int
                or self.generation_count < len(self.births)):
            raise ValueError("generation count cannot be smaller than the prefix")
        if (not self.entry_nodes or any(type(node) is not int or node < 0
                                       for node in self.entry_nodes)):
            raise ValueError("entry pool must contain distinct nonnegative integers")
        pool = set(self.entry_nodes)
        if len(pool) != len(self.entry_nodes):
            raise ValueError("entry pool must contain distinct nonnegative integers")
        if self.arrivals == "bursty":
            if self.phase_ms is None or not 0 <= self.phase_ms < self.period_ms:
                raise ValueError("bursty phase must lie within one period")
        elif self.phase_ms is not None:
            raise ValueError("only bursty arrivals have a phase")
        previous = -1.0
        for birth in self.births:
            if not isinstance(birth, Birth):
                raise ValueError("manifest entries must be Birth records")
            if birth.time_ms < previous or birth.time_ms >= self.window_ms:
                raise ValueError("births must be ordered within the birth window")
            if birth.node_id not in pool:
                raise ValueError("birth entry is outside the manifest entry pool")
            previous = birth.time_ms

    def metadata(self):
        mixture = {KIND_PQ: 0, KIND_PRIVACY: 0}
        for birth in self.births:
            mixture[birth.kind] += 1
        return {
            "manifest_version": self.version,
            "arrivals": self.arrivals,
            "realised_mix": mixture,
            "manifest_window_ms": self.window_ms,
            "arrival_period_ms": self.period_ms,
            "arrival_phase_ms": self.phase_ms,
            "manifest_entry_nodes": list(self.entry_nodes),
            "generation_window_ms": self.generation_window_ms,
            "generation_count": self.generation_count,
        }

    def to_record(self):
        record = self.metadata()
        record["births"] = [asdict(birth) for birth in self.births]
        return record

    @classmethod
    def from_record(cls, record):
        manifest = cls(
            tuple(Birth(**birth) for birth in record["births"]),
            record["arrivals"], record["manifest_window_ms"],
            record["arrival_period_ms"], record["arrival_phase_ms"],
            tuple(record["manifest_entry_nodes"]),
            record["generation_window_ms"], record["generation_count"],
            record["manifest_version"])
        if record["realised_mix"] != manifest.metadata()["realised_mix"]:
            raise ValueError("declared mixture differs from the birth records")
        return manifest

    def prefix(self, until_ms):
        """Keep births strictly before the cutoff, without regenerating any."""
        if not math.isfinite(until_ms) or not 0 < until_ms <= self.window_ms:
            raise ValueError("prefix cutoff must lie within the birth window")
        births = tuple(birth for birth in self.births if birth.time_ms < until_ms)
        return WorkloadManifest(
            births, self.arrivals, until_ms, self.period_ms, self.phase_ms,
            self.entry_nodes, self.generation_window_ms, self.generation_count,
            self.version)

    def schedule(self, sim):
        """Consume literal records; no random choice or mixture adjustment."""
        if any(node >= len(sim.nodes) for node in self.entry_nodes):
            raise ValueError("manifest entry pool exceeds the simulation nodes")
        if any(birth.time_ms < sim.engine.now for birth in self.births):
            raise ValueError("manifest contains births before the current clock")
        if sim.workload_generator_version not in (-1, self.version):
            raise ValueError("manifest version disagrees with this run's generator")
        sim.workload_generator_version = self.version
        for birth in self.births:
            sim.schedule_birth(birth.time_ms, birth.kind, node_id=birth.node_id,
                               fee=birth.fee, lifetime=birth.lifetime_ms)


def arrival_times(count, window_ms, period_ms, profile, seed):
    """Fixed-count ordered times; nonuniform profiles use separate streams."""
    if type(count) is not int or count < 0:
        raise ValueError("arrival count must be a nonnegative integer")
    if profile not in ARRIVAL_PROFILES:
        raise ValueError("unknown arrival profile")
    for duration in (window_ms, period_ms):
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("arrival durations must be finite and positive")
    if profile == "uniform":
        spacing = window_ms / max(1, count)
        return [index * spacing for index in range(count)], None
    rng = random.Random(f"{seed}:arrivals:{profile}:v{MANIFEST_VERSION}")
    if profile == "poisson":
        last = math.nextafter(window_ms, 0.0)
        return sorted(min(rng.random() * window_ms, last)
                      for _ in range(count)), None

    periods = window_ms / period_ms
    if not math.isclose(periods, round(periods), rel_tol=1e-12):
        raise ValueError("generate bursty arrivals over whole periods; then prefix")

    phase_rng = random.Random(
        f"{seed}:arrivals:bursty-phase:v{MANIFEST_VERSION}")
    phase = phase_rng.random() * period_ms
    intervals = []
    ends = []
    active_ms = 0.0
    for cycle in range(-1, math.ceil(window_ms / period_ms)):
        start = phase + cycle * period_ms
        end = min(window_ms, start + period_ms / 5)
        start = max(0.0, start)
        if end > start:
            intervals.append((start, end))
            active_ms += end - start
            ends.append(active_ms)
    if count and not intervals:
        raise ValueError("birth window contains no active burst interval")
    times = []
    for _ in range(count):
        position = min(rng.random() * active_ms, math.nextafter(active_ms, 0.0))
        index = bisect_right(ends, position)
        before = 0.0
        if index:
            before = ends[index - 1]
        start, end = intervals[index]
        # Floating-point addition must not put a birth on the excluded end.
        time = min(start + (position - before), math.nextafter(end, start))
        times.append(time)
    return sorted(times), phase
