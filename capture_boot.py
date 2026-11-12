"""Capture the module->ATmega stream across the module's power-up, to find the
one-time handshake / write-enable the module sends before its steady poll loop.

Listen-only: FT232 RX -> J12.2, GND -> J12.4, TX DISCONNECTED, module unplugged.
Start this, then plug the module into J12 a few seconds in.

  python capture_boot.py COM9 30
"""
import sys
import time

import serial

STEADY = {0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x31, 0x35, 0x36, 0x37}


def main():
    port = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30

    with serial.Serial(port, 19200, timeout=0.02, write_timeout=0) as ser:
        ser.dtr = False
        ser.rts = False
        ser.reset_input_buffer()
        data = bytearray()
        stamp = []
        print(f"# capturing {secs:.0f}s on {port} - PLUG THE MODULE IN NOW")
        end = time.monotonic() + secs
        while time.monotonic() < end:
            b = ser.read(64)
            if b:
                t = time.monotonic()
                for _ in b:
                    stamp.append(t)
                data += b

    if not data:
        print("# nothing captured - check RX on J12.2 and that the module booted")
        return
    t0 = stamp[0]

    # parse AA ?? frames (either direction), fixed 7 bytes, valid checksum
    frames, i = [], 0
    while i < len(data) - 6:
        if data[i] == 0xAA and (sum(data[i:i + 6]) & 0xFF) == data[i + 6]:
            frames.append((stamp[i] - t0, bytes(data[i:i + 7])))
            i += 7
        else:
            i += 1

    first_steady = next((t for t, fr in frames if fr[2] in STEADY), None)
    print(f"# {len(data)} bytes, {len(frames)} frames; "
          f"first steady-poll op at t={first_steady}")
    print("# --- frames before/around first steady poll, and any NON-steady op ---")
    shown = 0
    for t, fr in frames:
        non_steady = fr[2] not in STEADY
        early = first_steady is None or t <= first_steady + 0.05
        if non_steady or early:
            mark = "  !!! NON-STEADY" if non_steady else ""
            print(f"  t={t:7.3f}  {fr.hex(' ')}{mark}")
            shown += 1
        if shown > 120:
            print("  ... (truncated)")
            break


if __name__ == "__main__":
    main()
