# Stepper Motor Sediment Feeder

> **Note:** This README was generated with AI assistance. While it reflects the actual code and hardware used in this project, some details (library versions, port names, wiring notes) may be inaccurate or become outdated. Verify against the source files and your hardware before following any instructions.

A CircuitPython firmware package for the Raspberry Pi Pico that drives a laboratory sediment feeder. A stepper motor controls a hopper/auger mechanism to dispense sediment at a programmable, adjustable rate. Speed and direction can be set manually via a rotary encoder and buttons (**MANUAL** mode), or automatically from a scheduler running on a connected PC such as a Raspberry Pi 4 (**AUTO** mode).

---

## Hardware Components

| Component | Description | Notes |
|---|---|---|
| Raspberry Pi Pico | RP2040 microcontroller | Standard Pico (not Pico W) |
| Stepper motor | NEMA stepper with step/dir/enable driver | Tested at 400 steps/rev |
| Stepper motor driver | A4988, DRV8825, or similar | Must expose STEP, DIR, and ENA pins |
| [Adafruit I2C Rotary Encoder Breakout](https://www.adafruit.com/product/4991) | Seesaw-based encoder with button | Product ID 4991, I2C addr `0x36` |
| SSD1306 OLED display | 128×64 monochrome, I2C | I2C addr `0x3D` |
| 2× momentary push buttons (B, C) | Through-hole or panel-mount | Pulled down internally via Pico GPIO |

---

## Pin Wiring

| Pico Pin | Function |
|---|---|
| GP4 (SDA) | I2C data — shared bus for OLED and encoder |
| GP5 (SCL) | I2C clock — shared bus for OLED and encoder |
| GP8 | Button C — toggle MANUAL / AUTO mode |
| GP9 | OLED reset |
| GP10 | Button B — reverse motor direction |
| GP15 | Button A — unused (may be left unwired) |
| GP18 | Stepper STEP (PWM output) |
| GP19 | Stepper DIR |
| GP22 | Stepper ENA |

The three GPIO buttons connect between the pin and GND; the firmware configures them with internal pull-downs, so they read HIGH when pressed.

---

## Software Requirements

### CircuitPython

Install **CircuitPython 9.2.x** on the Pico:

1. Download the UF2 from [circuitpython.org](https://circuitpython.org/board/raspberry_pi_pico/).
2. Hold BOOTSEL on the Pico and plug in USB; drag the UF2 to the `RPI-RP2` drive.

### Libraries

Copy the following from the [Adafruit CircuitPython Bundle](https://circuitpython.org/libraries) into the `lib/` folder on the Pico:

| Library | Source |
|---|---|
| `adafruit_seesaw/` | Adafruit CircuitPython Seesaw |
| `adafruit_displayio_ssd1306.py` | Adafruit CircuitPython DisplayIO SSD1306 |
| `adafruit_display_text/` | Adafruit CircuitPython Display Text |
| `adafruit_bus_device/` | Adafruit CircuitPython BusDevice |
| `adafruit_motor/` | Adafruit CircuitPython Motor |
| `adafruit_pixelbuf.mpy` | Adafruit CircuitPython Pixelbuf |
| `adafruit_ht16k33/` | Adafruit CircuitPython HT16K33 |

The following are built into CircuitPython and require no separate install: `pwmio`, `digitalio`, `displayio`, `busio`, `supervisor`, `usb_cdc`.

---

## Project Files

| File | Purpose |
|---|---|
| `boot.py` | Runs at power-on; enables dual USB CDC endpoints (console + data) |
| `stepper.py` | `Stepper` class — low-level motor control via PWM and GPIO |
| `controller.py` | Hardware-free logic: modes, speed setpoint, serial protocol, link-loss watchdog |
| `code.py` | Main application — encoder, buttons, OLED, serial; glue between hardware and `controller.py` |
| `host/` | PC-side scheduler (`feeder_host`), example schedule, `systemd` unit |
| `tests/` | `pytest` tests for the controller, the schedule and the scheduler end-to-end |

## Getting Started

1. Wire up the hardware per the pin table above.
2. Flash CircuitPython and copy the `lib/` folder to the Pico.
3. Copy `boot.py`, `stepper.py`, `controller.py`, and `code.py` to the root of the Pico (`CIRCUITPY` drive).
4. The Pico will reboot and start the feeder firmware automatically.

The OLED shows live status, starting in MANUAL with the motor stopped:

```
MANUAL
speed: 0.080 rps
motor: OFF
dir: CW
knob control
```

---

## Operating modes and controls

The OLED shows the current mode in large text (`MANUAL` or `AUTO`), plus speed (rps), motor state, direction and control source.

| Control | MANUAL | AUTO |
|---|---|---|
| Rotate encoder | change speed (0.02 rps per detent) | **take over**: drop to MANUAL, motor stopped |
| Press encoder | motor start / stop | **take over**: drop to MANUAL, motor stopped |
| Button B (GP10) | reverse direction | reverse direction |
| Button C (GP8) | switch to AUTO (motor stopped) | switch to MANUAL (motor stopped) |

* The motor is always **stopped** when the mode changes, so nothing starts unexpectedly.
* In AUTO the speed comes from the PC. Any command from the PC is a heartbeat. If none arrives for 10 s (`AUTO_TIMEOUT_S` in `code.py`) the motor **stops**, the Pico returns to MANUAL, and the OLED shows `LINK LOST - STOPPED`. Unplugging the USB cable therefore stops the feeder.
* The slowest achievable speed is `10 Hz / steps_per_rev` (0.025 rps at 400 steps/rev). Requests between 0 and that floor are rejected by the PC protocol; the encoder steps from 0 straight to the floor.
* Button C no longer exits the program. Send `stop` over serial to do that.

---

## Scheduled feeding from a PC

```
cd stepper_control/host
pip install -r requirements.txt
python -m feeder_host check schedule.example.toml          # validate and print the plan
python -m feeder_host run schedule.example.toml \
    --port /dev/serial/by-id/<pico>-if02 --csv ~/feed-log.csv
```

1. On the box, press **button C** to switch to AUTO (or let the scheduler request it with `mode=auto`).
2. The scheduler walks through the schedule, sending the rate for each segment and a heartbeat every second, and prints a readout in both rps and g/min:
   `00:01:05 seg 2/3 steady     cmd 0.0625 rps (12.000 g/min) | pico AUTO running 0.0625 rps`
3. At the end (or on Ctrl-C) it sends `rate=0` and `mode=manual`, handing control back to the knob, stopped.
4. If the operator uses the encoder during a run, the scheduler notices (`mode` is no longer AUTO), logs it and exits; it never takes control back by itself.

### Schedule file (TOML)

```toml
[calibration]
grams_per_rev = 3.2        # required: measured grams per auger revolution

[[segment]]
label = "steady"
duration = "1h30m"         # 90, "30s", "10m", "1h30m", ...
rate_g_per_min = 12.0      # or rate_rps = 0.0625; rate_rps = 0 stops the motor

[repeat]
count = 1                  # 0 = loop until stopped
```

`rps = g_per_min / 60 / grams_per_rev`. Segment rates above `--max-rps` (default 2.0) are rejected when the file is loaded.

### Running on a Raspberry Pi at boot

Use the stable port name from `ls /dev/serial/by-id/` (the **data** port is the `-if02` one; `ttyACM0/1` order is not guaranteed). `host/feeder-scheduler.service` is a `systemd` template.

### Tests

Run from the **repository root** (running pytest inside `stepper_control/` makes the firmware `code.py` shadow Python's `code` module):

```
python -m pytest stepper_control/tests
```

---

## PC-Side Python Setup

To send commands from a PC you need the `pyserial` package. Using a virtual environment keeps this isolated from your system Python.

### Create and activate a venv

**Linux / macOS:**
```bash
python3 -m venv feeder-env
source feeder-env/bin/activate
```

**Windows:**
```bat
python -m venv feeder-env
feeder-env\Scripts\activate
```

### Install dependencies

```bash
pip install pyserial
```

To save the environment for later:
```bash
pip freeze > requirements.txt
```

To restore it on another machine:
```bash
pip install -r requirements.txt
```

### Find the correct serial ports

When the Pico is plugged in, it will appear as two serial ports — one for the console and one for data. To find them:

**Linux:**
```bash
ls /dev/ttyACM*
# typically /dev/ttyACM0 (console) and /dev/ttyACM1 (data)
```

**macOS:**
```bash
ls /dev/tty.usbmodem*
```

**Windows:** Open Device Manager and look under **Ports (COM & LPT)** for two new COM ports.

---

## Serial protocol (USB data port)

Commands go to the **data** port, not the console, which is left free for debug output. One ASCII line in, one reply line out.

| Command | Reply | Notes |
|---|---|---|
| `ping`, `hb` | `ok` | link check / heartbeat |
| `mode=auto`, `mode=manual` | `ok mode=...` | motor is stopped on every switch |
| `rate=<rps>` | `ok` | AUTO only; `0` stops; `err range` outside [floor, max] |
| `get:status` | `status mode=AUTO running=1 rps=0.2500 dir=-1 link=ok` | |
| `speed=<rps>`, `toggle` | `ok` | MANUAL only (legacy) |
| `switch_direction` | `ok` | |
| `get:speed`, `get:direction`, `get:running`, `get:mode` | value | |
| `stop` | `bye` | stop the motor and exit the program |

Errors reply `err <reason>` (`empty`, `unknown`, `bad_number`, `bad_mode`, `range`, `not_auto`, `not_manual`). The old `enable` / `disable` commands were removed along with button A.

Quick manual test with pyserial:

```python
import serial
d = serial.Serial('/dev/ttyACM1', timeout=1)   # the data port
d.write(b'mode=auto\n'); print(d.readline())
d.write(b'rate=0.25\n'); print(d.readline())
d.write(b'get:status\n'); print(d.readline())
```

---

## Stepper Class Reference

`stepper.py` provides the `Stepper` class used by `code.py`, but can be imported into any CircuitPython project:

```python
from stepper import Stepper
import board

s = Stepper(
    step_pin=board.GP18,
    dir_pin=board.GP19,
    ena_pin=board.GP22,
    steps_per_rev=400,   # match your motor/driver microstepping setting
    speed_sps=800        # initial speed in steps per second
)

s.start()              # begin stepping
s.speed_rps(0.5)       # change to 0.5 RPS
s.change_direction()   # reverse
s.stop()               # halt and disable
```

`steps_per_rev` should match your driver's microstepping setting (e.g. 400 for half-step, 1600 for 1/8, 3200 for 1/16).

---

## License

MIT License — Eric Barefoot, 2025. Encoder example adapted from Adafruit (John Furcean, 2021, MIT).
