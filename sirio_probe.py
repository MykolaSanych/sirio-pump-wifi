"""Map the full set of registers/commands the ATmega answers, as J12 master.
WiNET only polls 10 ops; scan 0x01..0x7F to find everything readable (params?
pressure/freq/voltage?). Also probes a possible "read param N" (index in d2).

WIRING: module OUT, FT232 TX->J12.2, RX->J12.1, GND->J12.4, 5 V.
  python sirio_probe.py COM9
"""
import sys, time
import serial

port = sys.argv[1]
def build(op, d0=0, d1=0, d2=0):
    b = bytes([0xAA, 0xFF, op, d0, d1, d2]); return b + bytes([sum(b) & 0xFF])
def xfer(ser, op, d0=0, d1=0, d2=0, timeout=0.18):
    ser.reset_input_buffer(); ser.write(build(op, d0, d1, d2)); ser.flush()
    buf = bytearray(); end = time.monotonic() + timeout
    while time.monotonic() < end:
        buf += ser.read(64)
        for i in range(len(buf) - 6):
            if buf[i] == 0xAA and buf[i+1] == 0xFE and (sum(buf[i:i+6]) & 0xFF) == buf[i+6]:
                return bytes(buf[i:i+7])
    return None

with serial.Serial(port, 19200, timeout=0.05, write_timeout=1) as ser:
    ser.dtr = False; ser.rts = False
    print("=== scan read ops 0x01..0x7F ===")
    resp = {}
    for op in range(0x01, 0x80):
        r = xfer(ser, op) or xfer(ser, op)
        if r:
            resp[op] = r
            print("0x%02x -> %s" % (op, r.hex(' ')))
        time.sleep(0.02)
    print("responding ops:", [hex(o) for o in resp])

    # Probe a "read parameter N" style: try candidate ops with index in d2
    print("\n=== probe read-param (index in d2), ops 0x01..0x0f & 0x50..0x5f ===")
    for op in list(range(0x01, 0x10)) + list(range(0x50, 0x60)):
        rows = []
        for idx in (0, 2, 5, 16):
            r = xfer(ser, op, 0, 0, idx)
            rows.append(r[5] if r else None)
        if any(v is not None for v in rows):
            print("op 0x%02x  idx0,2,5,16 -> d2=%s" % (op, rows))
