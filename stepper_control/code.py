"""Sediment feeder controller for Raspberry Pi Pico (CircuitPython).

Hardware glue only: reads the encoder / buttons / USB data port, hands events to
`controller.Controller`, and applies its outputs to the `Stepper` and the OLED.

Controls
    Encoder turn     speed (0.02 rps per detent)         [MANUAL]
    Encoder switch   motor start / stop                  [MANUAL]
    Button B (GP10)  reverse direction                   [any mode]
    Button C (GP8)   toggle MANUAL <-> AUTO (PC control)
    Button A (GP15)  unused
In AUTO the motor is driven by the PC over the USB *data* port. Turning or pressing
the encoder in AUTO drops back to MANUAL with the motor stopped. If the PC goes
silent for AUTO_TIMEOUT_S the motor stops and the display shows LINK LOST.

SPDX-FileCopyrightText: 2021 John Furcean
SPDX-License-Identifier: MIT
Modified by Eric Barefoot MIT LICENSE 2025
"""

import board
import busio
import digitalio
import displayio
import i2cdisplaybus
import terminalio
import usb_cdc
import adafruit_displayio_ssd1306
from adafruit_display_text import label
from adafruit_seesaw import rotaryio, seesaw
from adafruit_seesaw import digitalio as ssdio
from supervisor import runtime
from time import monotonic_ns

from controller import Controller, AUTO
from stepper import Stepper

# ---- configuration -------------------------------------------------------

STEPS_PER_REV = 400      # must match the driver's microstepping setting
MAX_RPS = 2.0            # hard ceiling for both knob and PC
ENC_STEP_RPS = 0.02      # speed change per encoder detent
AUTO_TIMEOUT_S = 10.0    # PC silence before the motor is stopped
DEBOUNCE_S = 0.03

# Disable autoreload to prevent interruption during experiments
runtime.autoreload = False

T0 = monotonic_ns()


def now():
    return (monotonic_ns() - T0) * 1e-9


# ---- hardware ------------------------------------------------------------

stepper = Stepper(step_pin=board.GP18, dir_pin=board.GP19, ena_pin=board.GP22,
                  steps_per_rev=STEPS_PER_REV)
ctl = Controller(max_rps=MAX_RPS, min_rps=stepper.min_hz / STEPS_PER_REV,
                 enc_step_rps=ENC_STEP_RPS, auto_timeout=AUTO_TIMEOUT_S)
stepper.stop()
stepper.dir_pin.value = ctl.direction > 0  # clockwise at start

displayio.release_displays()
i2c = busio.I2C(scl=board.GP5, sda=board.GP4)

ss = seesaw.Seesaw(i2c, addr=0x36)
if (ss.get_version() >> 16) & 0xFFFF != 4991:
    print("Wrong firmware loaded?  Expected 4991")
ss.pin_mode(24, ss.INPUT_PULLUP)
enc_button = ssdio.DigitalIO(ss, 24)  # reads False when pressed
encoder = rotaryio.IncrementalEncoder(ss)


def make_button(pin):
    b = digitalio.DigitalInOut(pin)
    b.direction = digitalio.Direction.INPUT
    b.pull = digitalio.Pull.DOWN  # reads True when pressed
    return b


button_b = make_button(board.GP10)
button_c = make_button(board.GP8)


class Edge:
    """Debounced 'just pressed' detector for an active-high input."""

    def __init__(self, read):
        self.read = read
        self.state = False
        self.since = 0.0

    def pressed(self, t):
        v = self.read()
        if v != self.state and t - self.since > DEBOUNCE_S:
            self.state = v
            self.since = t
            return v
        return False


edge_enc = Edge(lambda: not enc_button.value)
edge_b = Edge(lambda: button_b.value)
edge_c = Edge(lambda: button_c.value)

# USB data port: PC commands in, replies out. The console stays free for debug.
data = usb_cdc.data
data.timeout = 0
data.write_timeout = 0.1
rx = bytearray()

# ---- display -------------------------------------------------------------

WIDTH, HEIGHT = 128, 64
display_bus = i2cdisplaybus.I2CDisplayBus(i2c, device_address=0x3D)
display = adafruit_displayio_ssd1306.SSD1306(display_bus, width=WIDTH, height=HEIGHT)
splash = displayio.Group()
display.root_group = splash


def add_label(y, scale=1):
    lbl = label.Label(terminalio.FONT, text="", color=0xFFFFFF, x=0, y=y, scale=scale)
    splash.append(lbl)
    return lbl


lbl_mode = add_label(8, scale=2)   # "AUTO" / "MANUAL"
lbl_speed = add_label(30)
lbl_motor = add_label(41)
lbl_dir = add_label(52)
lbl_link = add_label(62)
shown = [None] * 5


def set_text(i, lbl, text):
    if shown[i] != text:  # redrawing over I2C is slow, only touch what changed
        lbl.text = text
        shown[i] = text


def refresh_display():
    set_text(0, lbl_mode, ctl.mode)
    set_text(1, lbl_speed, "speed: %.3f rps" % ctl.rps)
    set_text(2, lbl_motor, "motor: " + ("ON" if ctl.running else "OFF"))
    set_text(3, lbl_dir, "dir: " + ("CW" if ctl.direction < 0 else "CCW"))
    if ctl.link_lost:
        link = "LINK LOST - STOPPED"
    elif ctl.mode == AUTO:
        link = "PC control"
    else:
        link = "knob control"
    set_text(4, lbl_link, link)


# ---- output application --------------------------------------------------

applied = {"running": False, "rps": None, "dir": None}


def apply_outputs():
    if ctl.direction != applied["dir"]:
        stepper.dir_pin.value = ctl.direction > 0
        applied["dir"] = ctl.direction
    if ctl.running:
        if ctl.rps != applied["rps"]:
            stepper.speed_rps(ctl.rps)
            applied["rps"] = ctl.rps
        if not applied["running"]:
            stepper.start()
            applied["running"] = True
    elif applied["running"]:
        stepper.stop()
        applied["running"] = False


def handle_serial():
    global rx
    n = data.in_waiting
    if n:
        rx += data.read(n)
    while True:
        i = rx.find(b"\n")
        if i < 0:
            break
        raw, rx = bytes(rx[:i]), rx[i + 1:]
        try:
            reply = ctl.handle_line(raw.decode(), now())
        except Exception as e:  # never let a bad line stop the control loop
            reply = "err " + repr(e)
        data.write((reply + "\n").encode())
    if len(rx) > 128:  # garbage without a newline
        rx = bytearray()


# ---- main loop -----------------------------------------------------------

last_pos = encoder.position
print("Feeder ready. min rps = %.4f" % ctl.min_rps)
refresh_display()

try:
    while not ctl.exit_requested:
        t = now()
        pos = encoder.position
        if pos != last_pos:
            ctl.encoder_delta(pos - last_pos, t)
            last_pos = pos
        if edge_enc.pressed(t):
            ctl.encoder_press(t)
        if edge_b.pressed(t):
            ctl.button_direction()
        if edge_c.pressed(t):
            ctl.button_mode(t)

        handle_serial()
        ctl.tick(now())
        apply_outputs()
        refresh_display()
finally:
    stepper.stop()  # always leave the motor stopped
    print("Program ended, motor stopped.")
