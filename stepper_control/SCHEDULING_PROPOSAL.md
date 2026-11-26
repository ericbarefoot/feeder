# Proposal: scheduled feed rate for the stepper feeder

Status: **implemented** (see README.md). Decisions below were confirmed by the owner; sections that
differ from what was built are noted in "Decisions".
Baseline reviewed: `sandpiper-toolchain/feeder` @ `6056510`, `stepper_control/` (`boot.py`, `stepper.py`, `code.py`).
This repo (`ericbarefoot/feeder`) does not yet contain `stepper_control/`; step 0 below is to bring it in.

## Goal

* Manual control on the box: encoder, encoder button, buttons A/B/C (as today).
* Automatic control: a connected PC (Pi 4 or similar) runs a schedule of feed rates.
* The operator can always take over at the box.
* If the PC dies, the feeder stops.

## What exists today (relevant findings)

* CircuitPython on the Pico, dual USB CDC (`boot.py`): console + data.
* `Stepper` sets STEP frequency by PWM, so speed is in RPS (`speed_rps`). There is no step counting.
* `code.py` is one monolithic loop. Encoder sets speed (0.02 RPS per detent). PC commands (`speed=`, `toggle`, `get:*`, ...) are read from the **console** port, and `get:` replies go to the **data** port.

Things that matter for scheduling:

1. **`speed(0)` does not stop the motor.** Below `min_hz` (10 Hz) it clamps to 10 Hz, so an "off" period in a schedule must call `stop()`, not set speed 0.
2. **`speed=` from the PC is only handled when the encoder did not move** (`elif` after the encoder check). The PC and knob fight, and the PC command is dropped if the knob moves.
3. **Commands share the console port with debug `print`s**, so a PC client cannot reliably parse replies. Replies for `get:` are on the other port.
4. **`conser.readline()` may block** if the console `timeout` is not 0 (needs on-hardware check). A blocked loop would also stop the encoder.
5. **No fail-safe.** If the PC link drops mid-schedule the motor keeps running at the last speed.
6. Button C **exits the program**, which is easy to hit by accident mid-experiment.
7. `stop()` also de-asserts ENA, so coils are released between feed pulses (fine for an auger, but worth knowing).

## Design

### Principle

The Pico stays a simple, safe actuator. The PC owns time and the schedule.

```
 Pi 4 / PC                                  Pico
 ┌─────────────────────┐   USB CDC data    ┌───────────────────────────┐
 │ feeder_scheduler.py │ ───────────────►  │ command parser (non-block)│
 │  - reads schedule   │  setpoint + 1 Hz  │ mode: MANUAL | AUTO       │
 │  - sends setpoints  │  heartbeat        │ watchdog (AUTO only)      │
 │  - logs status      │ ◄───────────────  │ Stepper (unchanged API)   │
 └─────────────────────┘   status lines    │ encoder / buttons / OLED  │
                                            └───────────────────────────┘
```

### 1. Firmware (CircuitPython, `stepper_control/`)

* **Two modes.** `MANUAL` (today's behaviour, encoder is the source of speed) and `AUTO` (speed comes from the PC). Mode is shown on the OLED.
* **Knob precedence (operator always wins):**
  * Button C becomes a **mode toggle** (MANUAL <-> AUTO). "Exit program" moves to the serial `stop` command only.
  * Turning the encoder or pressing its button while in AUTO drops to MANUAL with the motor **stopped**; the operator must restart deliberately.
  * Buttons A (enable) and B (direction) work in both modes.
* **Commands move to the data port** (both directions), so the console stays free for debug output. Line-based ASCII, one reply per command:

  | Command | Reply | Notes |
  |---|---|---|
  | `ping` | `ok` | link check |
  | `mode=auto` / `mode=manual` | `ok mode=...` | switch from PC |
  | `rate=<rps>` | `ok` | AUTO only; `rate=0` calls `stop()` |
  | `hb` | `ok` | optional explicit heartbeat (any valid command also counts) |
  | `get:status` | `status mode=AUTO running=1 rps=0.250 dir=-1 ena=1 age=0.4` | one line, easy to parse |
  | existing `speed=`, `toggle`, `get:speed`... | unchanged | kept for compatibility |

* **Watchdog:** in AUTO, if no valid command arrives for `AUTO_TIMEOUT_S` (default 10 s), call `stop()` and fall back to MANUAL (stopped), and show `LINK LOST` on the OLED.
* **Non-blocking reader:** set `usb_cdc.data.timeout = 0` and accumulate bytes into a buffer, dispatching on `\n`.
* **Refactor for testability:** split the loop into a small pure-Python `controller.py` (mode, setpoint, watchdog, command parser: no hardware imports) plus the existing hardware glue in `code.py`. The controller can then be unit-tested on a laptop with plain `pytest`.
* **Ramping** (optional, recommended): limit acceleration (e.g. 0.5 RPS/s) in the controller so step changes in a schedule do not stall the stepper.

### 2. PC side (`stepper_control/host/`, Python + `pyserial`)

* `feeder_scheduler.py`: reads a schedule file, opens the data port, sends `mode=auto`, then for each segment sends `rate=` at segment boundaries and a heartbeat every 1 s, polling `get:status` and logging to CSV (timestamp, commanded rate, reported rate, mode).
* **Schedule format:** TOML, human-editable, e.g.

  ```toml
  [calibration]
  grams_per_rev = 3.2          # optional; lets you schedule in g/min

  [[segment]]
  duration = "10m"
  rate_rps = 0.20

  [[segment]]
  duration = "5m"
  rate_rps = 0            # off

  [[segment]]
  duration = "30m"
  rate_g_per_min = 12.0   # converted using grams_per_rev

  [repeat]
  count = 0               # 0 = loop until stopped
  ```
* **Timing:** the host uses a monotonic clock, so segment times don't drift with the serial latency. Missed segments are not "caught up" after a pause; the schedule resumes at the current wall-clock position.
* **If the operator takes over at the box** (status shows `mode=MANUAL`), the scheduler logs it and stops sending, instead of re-grabbing control. Resume is explicit (`--resume` or restart).
* **Pi 4 deployment:** a `systemd` unit runs the script on boot. Port selection uses the stable `/dev/serial/by-id/...-if02` (data) link rather than `ttyACM0/1`, since enumeration order is not guaranteed.
* Dependencies: `pyserial` only (`tomllib` is in the stdlib on Python 3.11+, i.e. Raspberry Pi OS Bookworm).

### 3. Testing

* Unit tests (`pytest`) for `controller.py` (mode transitions, watchdog, command parsing, ramp) and the schedule parser/calendar logic on a laptop.
* A fake serial transport to run the scheduler end-to-end against `controller.py` with no hardware.
* On-hardware checklist: knob takeover during AUTO, USB unplug during AUTO (motor must stop within the timeout), `rate=0` really stops, lowest usable rate at your microstepping.

## Delivery plan

0. Bring `stepper_control/` from upstream into this repo (no changes).
1. Firmware: extract `controller.py`, add modes, data-port protocol, watchdog, non-blocking reader, fix findings 1-6. Tests.
2. Host scheduler + schedule format + logging. Tests with fake transport.
3. Pi 4 packaging (`systemd` unit, `by-id` port setup) and docs; update `stepper_control/README.md`.

## Decisions (from review)

* Encoder switch = motor on/off, encoder rotation = speed. Button B (direction) is kept; button A is dropped.
* Button C = MANUAL/AUTO toggle, shown on the OLED. Exit-program is serial `stop` only.
* Schedules require a calibration (`grams_per_rev`) and show both rps and g/min.
* Link loss stops the motor.
* Not built: acceleration ramping, direction reversal in schedules (can be added later).

## Open questions (original, now answered)

1. **"Selector knobs":** I assumed the encoder plus buttons A/B/C. Is there also a rate-selector pot or switch I should account for?
2. **Button C:** OK to repurpose it from "exit program" to "mode toggle"?
3. **Schedule units:** is RPS enough, or do you want g/min using a measured grams-per-rev calibration?
4. **Fail-safe:** on link loss, stop (proposed) or hold last rate?
5. **Direction reversal / anti-jam pulses** in the schedule: needed?
6. **Pico stays CircuitPython?** I assumed yes (upstream's choice).
