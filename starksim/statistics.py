"""Fixed-storage distributions of nonnegative samples."""

from bisect import bisect_right


# Zero, positive underflow, powers of two, and positive overflow are separate.
POWER_MINIMUMS = tuple(2.0 ** exponent for exponent in range(-20, 41))


class Moments:
    """Fixed-storage moments and histogram; no sample history is retained."""

    def __init__(self):
        self.count = 0
        self.sum = 0.0
        self.square_sum = 0.0
        self.maximum = -1.0
        self.histogram = [0] * (len(POWER_MINIMUMS) + 2)

    def record(self, value):
        self.count += 1
        self.sum += value
        self.square_sum += value * value
        self.maximum = max(self.maximum, value)
        bucket = 0
        if value > 0:
            bucket = 1 + bisect_right(POWER_MINIMUMS, value)
        self.histogram[bucket] += 1

    def merge(self, other):
        self.count += other.count
        self.sum += other.sum
        self.square_sum += other.square_sum
        self.maximum = max(self.maximum, other.maximum)
        for index, count in enumerate(other.histogram):
            self.histogram[index] += count

    def record_dict(self):
        return {"count": self.count, "sum": self.sum,
                "square_sum": self.square_sum, "max": self.maximum,
                "histogram": list(self.histogram)}
