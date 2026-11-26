"""Command line: python -m feeder_host run schedule.toml --port /dev/serial/by-id/..."""

import argparse
import sys

from . import schedule as sched_mod
from .link import SerialLink
from .runner import Runner, fmt_hms


def main(argv=None):
    p = argparse.ArgumentParser(prog="feeder_host")
    sub = p.add_subparsers(dest="cmd", required=True)

    chk = sub.add_parser("check", help="validate a schedule and print the plan")
    chk.add_argument("schedule")
    chk.add_argument("--max-rps", type=float, default=2.0)

    run = sub.add_parser("run", help="run a schedule on the feeder")
    run.add_argument("schedule")
    run.add_argument("--port", required=True, help="Pico USB *data* port")
    run.add_argument("--csv", help="append a CSV log here")
    run.add_argument("--max-rps", type=float, default=2.0)

    args = p.parse_args(argv)
    sch = sched_mod.load(args.schedule, max_rps=args.max_rps)
    print("calibration: %.4f g/rev" % sch.grams_per_rev)
    for i, s in enumerate(sch.segments, 1):
        print("  %d. %-10s %9s  %.4f rps  %.3f g/min"
              % (i, s.label, fmt_hms(s.duration), s.rps, sch.rps_to_g_per_min(s.rps)))
    total = "forever" if sch.total is None else fmt_hms(sch.total)
    print("total: %s" % total)
    if args.cmd == "check":
        return 0

    result = Runner(SerialLink(args.port), sch, csv_path=args.csv).run()
    print("result:", result)
    return 0 if result in ("finished", "interrupted") else 1


if __name__ == "__main__":
    sys.exit(main())
