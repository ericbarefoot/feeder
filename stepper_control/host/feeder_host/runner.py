"""Runs a Schedule against a link: setpoints, heartbeat, readout, CSV log."""

import csv
import time

from .link import LinkError, parse_status


def fmt_hms(seconds):
    s = int(seconds)
    return "%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


class Runner:
    def __init__(self, link, schedule, clock=time.monotonic, sleep=time.sleep,
                 csv_path=None, out=print, hb_interval=1.0, tick=0.2, max_failures=5):
        self.link, self.schedule = link, schedule
        self.clock, self.sleep, self.out = clock, sleep, out
        self.hb_interval, self.tick, self.max_failures = hb_interval, tick, max_failures
        self._csv_file = open(csv_path, "a", newline="") if csv_path else None
        self._csv = csv.writer(self._csv_file) if self._csv_file else None
        if self._csv and self._csv_file.tell() == 0:
            self._csv.writerow(["unix_time", "elapsed_s", "segment", "cmd_rps",
                                "cmd_g_per_min", "pico_mode", "pico_running", "pico_rps"])

    def _cmd(self, command):
        return self.link.request(command)

    def run(self):
        """Returns 'finished', 'takeover' (operator used the knob) or 'link_lost'."""
        sched = self.schedule
        reply = self._cmd("mode=auto")
        if not reply.startswith("ok"):
            raise LinkError("Pico refused AUTO: %s" % reply)
        t0 = self.clock()
        last_sent_rps, last_hb, failures = None, -1e9, 0
        result = "finished"
        try:
            while True:
                elapsed = self.clock() - t0
                hit = sched.at(elapsed)
                if hit is None:
                    break
                idx, seg, _ = hit
                due = elapsed - last_hb >= self.hb_interval
                if seg.rps != last_sent_rps or due:
                    try:
                        reply = self._cmd("rate=%.5f" % seg.rps)
                        if not reply.startswith("ok"):
                            if reply == "err not_auto":
                                result = "takeover"
                                self.out("Operator left AUTO mode; scheduler stopping.")
                                break
                            raise LinkError("rate rejected: %s" % reply)
                        last_sent_rps = seg.rps
                        status = parse_status(self._cmd("get:status"))
                        failures = 0
                    except LinkError as e:
                        failures += 1
                        self.out("link error (%d/%d): %s" % (failures, self.max_failures, e))
                        if failures >= self.max_failures:
                            result = "link_lost"
                            break
                        status = None
                    if due:
                        last_hb = elapsed
                    if status is not None:
                        if status.get("mode") != "AUTO":
                            result = "takeover"
                            self.out("Operator took over at the box; scheduler stopping.")
                            break
                        self._report(elapsed, idx, seg, status)
                self.sleep(self.tick)
        except KeyboardInterrupt:
            result = "interrupted"
        finally:
            if result != "link_lost":
                self._finish(result)
            if self._csv_file:
                self._csv_file.close()
        return result

    def _finish(self, result):
        # Hand control back stopped. After a takeover the Pico is already in MANUAL.
        try:
            if result != "takeover":
                self._cmd("rate=0")
                self._cmd("mode=manual")
        except LinkError:
            self.out("could not send stop; the Pico watchdog will stop the motor")

    def _report(self, elapsed, idx, seg, status):
        gpm = self.schedule.rps_to_g_per_min(seg.rps)
        self.out("%s seg %d/%d %-10s cmd %.4f rps (%.3f g/min) | pico %s %s %.4f rps"
                 % (fmt_hms(elapsed), idx + 1, len(self.schedule.segments), seg.label,
                    seg.rps, gpm, status.get("mode"),
                    "running" if status.get("running") == "1" else "stopped",
                    float(status.get("rps", 0))))
        if self._csv:
            self._csv.writerow([round(time.time(), 3), round(elapsed, 2), idx + 1,
                                seg.rps, round(gpm, 4), status.get("mode"),
                                status.get("running"), status.get("rps")])
            self._csv_file.flush()
