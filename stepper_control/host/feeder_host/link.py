"""Serial link to the Pico's USB data port (one reply line per command)."""


class LinkError(Exception):
    pass


class SerialLink:
    def __init__(self, port, baud=115200, timeout=1.0):
        import serial  # imported late so tests don't need pyserial
        self.ser = serial.Serial(port, baud, timeout=timeout)

    def request(self, command):
        self.ser.reset_input_buffer()
        self.ser.write((command.strip() + "\n").encode())
        line = self.ser.readline().decode(errors="replace").strip()
        if not line:
            raise LinkError("no reply to %r" % command)
        return line

    def close(self):
        self.ser.close()


def parse_status(line):
    """'status mode=AUTO rps=0.2 ...' -> {'mode': 'AUTO', 'rps': '0.2', ...}"""
    if not line.startswith("status "):
        raise LinkError("not a status line: %r" % line)
    return dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)
