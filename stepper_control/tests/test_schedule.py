import pytest
from feeder_host import schedule as S


def sample(**extra):
    d = {"calibration": {"grams_per_rev": 3.0},
         "segment": [{"duration": "10m", "rate_rps": 0.1, "label": "a"},
                     {"duration": "1m30s", "rate_g_per_min": 9.0},
                     {"duration": 60, "rate_rps": 0}]}
    d.update(extra)
    return d


def test_duration_parsing():
    assert S.parse_duration("30s") == 30
    assert S.parse_duration("1h30m") == 5400
    assert S.parse_duration("2.5m") == 150
    assert S.parse_duration(45) == 45
    for bad in ("", "abc", "-5", 0, "10x"):
        with pytest.raises(S.ScheduleError):
            S.parse_duration(bad)


def test_calibration_conversion_both_ways():
    s = S.from_dict(sample())
    assert s.segments[1].rps == pytest.approx(9.0 / 60 / 3.0)   # 0.05 rps
    assert s.rps_to_g_per_min(0.05) == pytest.approx(9.0)
    assert s.g_per_min_to_rps(9.0) == pytest.approx(0.05)


def test_calibration_required():
    d = sample()
    del d["calibration"]
    with pytest.raises(S.ScheduleError):
        S.from_dict(d)
    with pytest.raises(S.ScheduleError):
        S.from_dict(sample(calibration={"grams_per_rev": 0}))


def test_segment_needs_exactly_one_rate():
    base = {"calibration": {"grams_per_rev": 1}}
    for seg in ({"duration": 1}, {"duration": 1, "rate_rps": 1, "rate_g_per_min": 1}):
        with pytest.raises(S.ScheduleError):
            S.from_dict({**base, "segment": [seg]})


def test_max_rps_enforced():
    with pytest.raises(S.ScheduleError):
        S.from_dict(sample(), max_rps=0.05)


def test_lookup_single_pass():
    s = S.from_dict(sample())
    assert s.total == 600 + 90 + 60
    assert s.at(0)[0] == 0
    assert s.at(599.9)[0] == 0
    assert s.at(600)[0] == 1
    assert s.at(689.9)[0] == 1
    assert s.at(690)[0] == 2
    assert s.at(750) is None
    assert s.next_boundary(10) == 600


def test_lookup_repeat_and_forever():
    s = S.from_dict(sample(repeat={"count": 2}))
    assert s.total == 1500
    assert s.at(750 + 5)[0] == 0
    assert s.at(1500) is None
    f = S.from_dict(sample(repeat={"count": 0}))
    assert f.total is None
    assert f.at(10 * 750 + 650)[0] == 1


def test_example_file_loads():
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "host", "schedule.example.toml")
    s = S.load(path, max_rps=2.0)
    assert len(s.segments) == 3 and s.grams_per_rev == 3.2
