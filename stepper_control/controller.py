"""Hardware-free control logic for the feeder (runs on CircuitPython and CPython).

Owns the operating mode, the speed setpoint, the serial command protocol and the
link-loss watchdog. `code.py` feeds it input events (encoder, buttons, serial lines)
and applies its outputs (`running`, `rps`, `direction`) to the `Stepper`.

Modes
    MANUAL  the encoder sets speed, the encoder switch starts/stops the motor.
    AUTO    speed comes from the PC (`rate=<rps>`). Any PC command counts as a
            heartbeat; if none arrives for `auto_timeout` seconds the motor is
            stopped and the controller falls back to MANUAL (stopped).

Operator precedence: turning or pressing the encoder while in AUTO drops to MANUAL
with the motor stopped.

Serial protocol: one ASCII line per command, one reply line per command.
    ping | hb                  -> ok
    mode=auto | mode=manual    -> ok mode=AUTO|MANUAL
    rate=<rps>                 -> ok            (AUTO only; 0 stops the motor)
    get:status                 -> status mode=AUTO running=1 rps=0.250 dir=-1 link=ok
    speed=<rps>                -> ok            (MANUAL only, legacy)
    toggle                     -> ok            (MANUAL only, legacy)
    switch_direction           -> ok
    get:speed|direction|running|mode -> value
    stop                       -> bye           (asks code.py to exit the program)
Errors reply `err <reason>`.
"""

MANUAL = "MANUAL"
AUTO = "AUTO"


class Controller:
    def __init__(self, max_rps=2.0, min_rps=0.0, enc_step_rps=0.02,
                 auto_timeout=10.0, initial_rps=0.08):
        self.max_rps = max_rps
        self.min_rps = min_rps  # slowest rate the stepper can really do
        self.enc_step_rps = enc_step_rps
        self.auto_timeout = auto_timeout

        self.mode = MANUAL
        self.direction = -1  # -1 clockwise, 1 counter-clockwise (as in stepper.py)
        self.link_lost = False
        self.exit_requested = False

        self._manual_rps = self._quantize(initial_rps)
        self._manual_running = False
        self._auto_rps = 0.0
        self._last_cmd = 0.0

    # ---- outputs ---------------------------------------------------------

    @property
    def rps(self):
        return self._auto_rps if self.mode == AUTO else self._manual_rps

    @property
    def running(self):
        return self.rps > 0 and (self.mode == AUTO or self._manual_running)

    # ---- helpers ---------------------------------------------------------

    def _quantize(self, rps):
        """Clamp to [0, max]; rates between 0 and min_rps are not achievable."""
        if rps <= 0:
            return 0.0
        if rps > self.max_rps:
            return self.max_rps
        if rps < self.min_rps:
            return self.min_rps if rps >= self.min_rps / 2 else 0.0
        return rps

    def _to_manual_stopped(self):
        self.mode = MANUAL
        self._manual_running = False
        self._auto_rps = 0.0

    def _to_auto(self, now):
        self.mode = AUTO
        self._manual_running = False
        self._auto_rps = 0.0
        self._last_cmd = now
        self.link_lost = False

    # ---- operator inputs -------------------------------------------------

    def encoder_delta(self, delta, now):
        """Encoder turned by `delta` detents."""
        if delta == 0:
            return
        if self.mode == AUTO:
            self._to_manual_stopped()
        target = self._manual_rps + delta * self.enc_step_rps
        if delta < 0 and 0 < target < self.min_rps:
            target = 0.0  # stepping down below the floor goes to 0
        elif delta > 0 and 0 < target < self.min_rps:
            target = self.min_rps
        self._manual_rps = self._quantize(target)

    def encoder_press(self, now):
        """Encoder switch pressed: start/stop (or take over from AUTO, stopped)."""
        if self.mode == AUTO:
            self._to_manual_stopped()
        else:
            self._manual_running = not self._manual_running

    def button_direction(self):
        """Button B."""
        self.direction = -self.direction

    def button_mode(self, now):
        """Button C: toggle MANUAL <-> AUTO. The motor is stopped on every switch."""
        if self.mode == AUTO:
            self._to_manual_stopped()
        else:
            self._to_auto(now)

    # ---- time ------------------------------------------------------------

    def tick(self, now):
        """Call every loop iteration: enforces the link-loss watchdog."""
        if self.mode == AUTO and now - self._last_cmd > self.auto_timeout:
            self._to_manual_stopped()
            self.link_lost = True

    # ---- serial protocol -------------------------------------------------

    def status(self, now):
        return "status mode=%s running=%d rps=%.4f dir=%d link=%s" % (
            self.mode, 1 if self.running else 0, self.rps, self.direction,
            "lost" if self.link_lost else "ok")

    def handle_line(self, line, now):
        """Process one command line. Returns the reply string (never None)."""
        line = line.strip().lower()
        if not line:
            return "err empty"
        if self.mode == AUTO:
            self._last_cmd = now  # any command from the PC is a heartbeat

        if line in ("ping", "hb"):
            return "ok"
        if line == "get:status":
            return self.status(now)
        if line == "get:speed":
            return "%.4f" % self.rps
        if line == "get:direction":
            return "%d" % self.direction
        if line == "get:running":
            return "True" if self.running else "False"
        if line == "get:mode":
            return self.mode
        if line == "switch_direction":
            self.button_direction()
            return "ok"
        if line == "stop":
            self._to_manual_stopped()
            self.exit_requested = True
            return "bye"
        if line == "toggle":
            if self.mode != MANUAL:
                return "err not_manual"
            self._manual_running = not self._manual_running
            return "ok"

        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key == "mode":
                if value == "auto":
                    if self.mode != AUTO:
                        self._to_auto(now)
                    return "ok mode=AUTO"
                if value == "manual":
                    self._to_manual_stopped()
                    return "ok mode=MANUAL"
                return "err bad_mode"
            if key in ("rate", "speed"):
                try:
                    x = float(value)
                except ValueError:
                    return "err bad_number"
                if x != x or x < 0 or x > self.max_rps:
                    return "err range"
                if key == "rate":
                    if self.mode != AUTO:
                        return "err not_auto"
                    if 0 < x < self.min_rps:
                        return "err range"
                    self._auto_rps = x
                    return "ok"
                if self.mode != MANUAL:
                    return "err not_manual"
                if 0 < x < self.min_rps:
                    return "err range"
                self._manual_rps = x
                return "ok"
        return "err unknown"
