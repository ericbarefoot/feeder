"""End-to-end: Runner -> FakeLink -> real Controller, on a simulated clock."""
import pytest
from controller import Controller, AUTO, MANUAL
from feeder_host import schedule as S
from feeder_host.link import LinkError
from feeder_host.runner import Runner


class World:
    """Fake clock + Pico (the real Controller) behind a link that can be cut."""

    def __init__(self):
        self.t = 0.0
        self.pico = Controller(max_rps=2.0, min_rps=0.025, auto_timeout=10.0)
        self.link_up = True
        self.sent = []
        self.history = []        # (time, rps actually applied by the Pico)
        self.hook = None

    def clock(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        self.pico.tick(self.t)
        self.history.append((self.t, self.pico.rps if self.pico.running else 0.0))
        if self.hook:
            self.hook(self)

    def request(self, cmd):
        if not self.link_up:
            raise LinkError("no reply")
        self.sent.append(cmd)
        return self.pico.handle_line(cmd, self.t)


def schedule(repeat=1):
    return S.from_dict({"calibration": {"grams_per_rev": 3.0},
                        "repeat": {"count": repeat},
                        "segment": [{"duration": 20, "rate_rps": 0.2, "label": "a"},
                                    {"duration": 10, "rate_rps": 0},
                                    {"duration": 15, "rate_g_per_min": 9.0}]})


def run(world, sch, **kw):
    lines = []
    r = Runner(world, sch, clock=world.clock, sleep=world.sleep, out=lines.append, **kw)
    return r.run(), lines


def applied_at(world, t):
    return min(world.history, key=lambda h: abs(h[0] - t))[1]


def test_full_run_follows_schedule_and_hands_back_stopped():
    w = World()
    result, lines = run(w, schedule())
    assert result == "finished"
    assert applied_at(w, 10) == pytest.approx(0.2)
    assert applied_at(w, 25) == 0
    assert applied_at(w, 40) == pytest.approx(0.05)      # 9 g/min / 3 g/rev
    assert w.pico.mode == MANUAL and not w.pico.running   # clean handoff
    assert any("0.2000 rps (36.000 g/min)" in l for l in lines)   # rps + g/min readout


def test_heartbeat_keeps_watchdog_quiet_on_long_segment():
    w = World()
    long = S.from_dict({"calibration": {"grams_per_rev": 1},
                        "segment": [{"duration": 120, "rate_rps": 0.1}]})
    assert run(w, long)[0] == "finished"
    assert not w.pico.link_lost
    assert applied_at(w, 100) == pytest.approx(0.1)


def test_link_loss_stops_motor_via_watchdog():
    w = World()
    w.hook = lambda w: setattr(w, "link_up", w.t < 12)   # cable pulled at t=12 s
    result, _ = run(w, schedule())
    assert result == "link_lost"       # the PC side gives up...
    assert w.pico.running              # ...but the Pico has not noticed yet
    last_contact = w.t
    while w.t < last_contact + 30:     # the Pico keeps running on its own
        w.sleep(0.2)
    assert w.pico.link_lost and w.pico.mode == MANUAL and not w.pico.running
    assert applied_at(w, 11) == pytest.approx(0.2)
    stop_t = min(t for t, r in w.history if t > last_contact and r == 0)
    # stopped by the watchdog within 10 s of the last command it received
    assert stop_t <= 12 + 10 + 1


def test_operator_takeover_ends_schedule_without_regrabbing():
    w = World()

    def knob(w):
        if 14 < w.t < 14.3:
            w.pico.encoder_delta(1, w.t)
    w.hook = knob
    result, _ = run(w, schedule())
    assert result == "takeover"
    assert w.pico.mode == MANUAL and not w.pico.running
    assert w.sent[-1] != "mode=auto"          # did not re-grab control
    assert "mode=manual" not in w.sent        # and did not override the operator


def test_pico_refusing_auto_raises():
    class Refuse(World):
        def request(self, cmd):
            return "err nope"
    with pytest.raises(LinkError):
        run(Refuse(), schedule())


def test_csv_log(tmp_path):
    w = World()
    p = tmp_path / "log.csv"
    run(w, schedule(), csv_path=str(p))
    rows = p.read_text().splitlines()
    assert rows[0].startswith("unix_time,elapsed_s")
    assert len(rows) > 10
