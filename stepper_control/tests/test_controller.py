from controller import Controller, AUTO, MANUAL


def make(**kw):
    return Controller(max_rps=2.0, min_rps=0.025, enc_step_rps=0.02,
                      auto_timeout=10.0, **kw)


def test_manual_encoder_and_switch():
    c = make()
    assert not c.running
    c.encoder_press(0)
    assert c.running and abs(c.rps - 0.08) < 1e-9
    c.encoder_delta(+3, 0)
    assert abs(c.rps - 0.14) < 1e-9
    c.encoder_press(0)
    assert not c.running


def test_encoder_floor_and_ceiling():
    c = make(initial_rps=0.04)
    c.encoder_delta(-1, 0)           # 0.02 is below the 0.025 floor -> 0
    assert c.rps == 0
    c.encoder_delta(+1, 0)           # 0.02 below floor going up -> floor
    assert c.rps == 0.025
    c.encoder_delta(+1000, 0)
    assert c.rps == 2.0
    c.encoder_delta(-1000, 0)
    assert c.rps == 0


def test_button_mode_toggles_and_stops():
    c = make()
    c.encoder_press(0)
    c.button_mode(1)
    assert c.mode == AUTO and not c.running
    c.handle_line("rate=0.5", 2)
    assert c.running and c.rps == 0.5
    c.button_mode(3)
    assert c.mode == MANUAL and not c.running


def test_knob_takeover_in_auto():
    for action in (lambda c: c.encoder_delta(1, 5), lambda c: c.encoder_press(5)):
        c = make()
        c.handle_line("mode=auto", 0)
        c.handle_line("rate=1.0", 1)
        assert c.running
        action(c)
        assert c.mode == MANUAL and not c.running


def test_direction_in_both_modes():
    c = make()
    d = c.direction
    c.button_direction()
    assert c.direction == -d
    c.handle_line("mode=auto", 0)
    c.button_direction()
    assert c.direction == d


def test_watchdog_stops_and_flags_link_lost():
    c = make()
    c.handle_line("mode=auto", 0)
    c.handle_line("rate=1.0", 1)
    c.tick(10)                       # 9 s since last command
    assert c.mode == AUTO and c.running
    c.handle_line("hb", 10)
    c.tick(19.5)
    assert c.mode == AUTO
    c.tick(20.5)
    assert c.mode == MANUAL and not c.running and c.link_lost
    assert "link=lost" in c.status(21)


def test_watchdog_when_pc_never_shows_up_after_button_c():
    c = make()
    c.button_mode(100)
    c.tick(105)
    assert c.mode == AUTO
    c.tick(111)
    assert c.mode == MANUAL and c.link_lost


def test_rate_validation():
    c = make()
    assert c.handle_line("rate=1", 0) == "err not_auto"
    c.handle_line("mode=auto", 0)
    assert c.handle_line("rate=abc", 1) == "err bad_number"
    assert c.handle_line("rate=-1", 1) == "err range"
    assert c.handle_line("rate=2.5", 1) == "err range"
    assert c.handle_line("rate=0.01", 1) == "err range"   # below stepper floor
    assert c.handle_line("rate=nan", 1) == "err range"
    assert c.handle_line("rate=0", 1) == "ok" and not c.running
    assert c.handle_line("RATE=0.5\n", 1) == "ok" and c.rps == 0.5
    assert c.handle_line("bogus", 1) == "err unknown"
    assert c.handle_line("", 1) == "err empty"


def test_rate_zero_stops_motor():
    c = make()
    c.handle_line("mode=auto", 0)
    c.handle_line("rate=1", 0)
    assert c.running
    c.handle_line("rate=0", 1)
    assert not c.running


def test_status_line_format():
    c = make()
    c.handle_line("mode=auto", 0)
    c.handle_line("rate=0.25", 0)
    assert c.handle_line("get:status", 0.4) == \
        "status mode=AUTO running=1 rps=0.2500 dir=-1 link=ok"


def test_legacy_commands():
    c = make()
    assert c.handle_line("speed=0.3", 0) == "ok" and c.rps == 0.3
    assert c.handle_line("toggle", 0) == "ok" and c.running
    assert c.handle_line("get:running", 0) == "True"
    assert c.handle_line("stop", 0) == "bye" and c.exit_requested and not c.running
