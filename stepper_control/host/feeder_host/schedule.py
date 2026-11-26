"""Feed-rate schedule: parsing, calibration and time lookup (no I/O besides the file)."""

import re

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ScheduleError(ValueError):
    pass


def parse_duration(value):
    """'90', 90, '30s', '10m', '1h30m' -> seconds (float)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            seconds = float(text)
        elif re.fullmatch(r"(\d+(\.\d+)?[smhd])+", text):
            seconds = sum(float(n) * _UNITS[u]
                          for n, u in re.findall(r"(\d+(?:\.\d+)?)([smhd])", text))
        else:
            raise ScheduleError("bad duration: %r" % (value,))
    if seconds <= 0:
        raise ScheduleError("duration must be > 0: %r" % (value,))
    return seconds


class Segment:
    def __init__(self, duration, rps, label=""):
        self.duration = duration
        self.rps = rps
        self.label = label


class Schedule:
    def __init__(self, grams_per_rev, segments, repeat=1):
        if grams_per_rev <= 0:
            raise ScheduleError("calibration.grams_per_rev must be > 0")
        if not segments:
            raise ScheduleError("schedule has no segments")
        if repeat < 0:
            raise ScheduleError("repeat.count must be >= 0 (0 = forever)")
        self.grams_per_rev = grams_per_rev
        self.segments = segments
        self.repeat = repeat  # 0 = loop forever
        self.cycle = sum(s.duration for s in segments)

    # calibration ----------------------------------------------------------
    def rps_to_g_per_min(self, rps):
        return rps * 60.0 * self.grams_per_rev

    def g_per_min_to_rps(self, g_per_min):
        return g_per_min / 60.0 / self.grams_per_rev

    # time lookup ----------------------------------------------------------
    @property
    def total(self):
        """Total run time in seconds, or None if it loops forever."""
        return None if self.repeat == 0 else self.cycle * self.repeat

    def at(self, elapsed):
        """-> (index, segment, seconds_into_segment), or None once finished."""
        if elapsed < 0:
            elapsed = 0.0
        if self.total is not None and elapsed >= self.total:
            return None
        t = elapsed % self.cycle
        for i, seg in enumerate(self.segments):
            if t < seg.duration:
                return i, seg, t
            t -= seg.duration
        return len(self.segments) - 1, self.segments[-1], self.segments[-1].duration

    def next_boundary(self, elapsed):
        """Elapsed time at which the current segment ends."""
        hit = self.at(elapsed)
        if hit is None:
            return elapsed
        _, seg, into = hit
        return elapsed + (seg.duration - into)


def from_dict(d, max_rps=None):
    cal = d.get("calibration")
    if not cal or "grams_per_rev" not in cal:
        raise ScheduleError("[calibration] grams_per_rev is required")
    gpr = float(cal["grams_per_rev"])
    if gpr <= 0:
        raise ScheduleError("calibration.grams_per_rev must be > 0")

    segments = []
    for n, raw in enumerate(d.get("segment", []), 1):
        has_rps = "rate_rps" in raw
        has_g = "rate_g_per_min" in raw
        if has_rps == has_g:
            raise ScheduleError(
                "segment %d: give exactly one of rate_rps / rate_g_per_min" % n)
        rps = float(raw["rate_rps"]) if has_rps else float(raw["rate_g_per_min"]) / 60.0 / gpr
        if rps < 0:
            raise ScheduleError("segment %d: rate must be >= 0" % n)
        if max_rps is not None and rps > max_rps:
            raise ScheduleError("segment %d: %.3f rps exceeds max %.3f" % (n, rps, max_rps))
        segments.append(Segment(parse_duration(raw.get("duration")), rps,
                                str(raw.get("label", ""))))
    repeat = int(d.get("repeat", {}).get("count", 1))
    return Schedule(gpr, segments, repeat)


def load(path, max_rps=None):
    with open(path, "rb") as f:
        return from_dict(tomllib.load(f), max_rps=max_rps)
