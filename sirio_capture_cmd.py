"""Capture the WRITE/command frames the module sends on J12.2 when we trigger an
HTTP action. Records the steady poll as a baseline, fires the action mid-window,
then reports any frame whose signature was not in the baseline (= the command),
plus a raw byte window around the action in case the write is a non-7-byte shape.

  python sirio_capture_cmd.py COM9 192.168.0.107 param 22
  python sirio_capture_cmd.py COM9 192.168.0.107 toggle
"""
import sys
import threading
import time

import serial

import sirio_api


def drain(ser, log, stop):
    while not stop.is_set():
        b = ser.read(256)
        if b:
            log.append((time.monotonic(), b))


def frames_from(chunks):
    """Flatten, return (t, 7-byte frame) for every AA-headed valid-checksum frame."""
    data = bytearray()
    times = []
    for t, b in chunks:
        for _ in b:
            times.append(t)
        data += b
    out = []
    i = 0
    while i < len(data) - 6:
        if data[i] == 0xAA and (sum(data[i:i + 6]) & 0xFF) == data[i + 6]:
            out.append((times[i], bytes(data[i:i + 7])))
            i += 7
        else:
            i += 1
    return out, bytes(data), times


def main():
    port, host, action = sys.argv[1], sys.argv[2], sys.argv[3]
    args = sys.argv[4:]

    ser = serial.Serial(port, 19200, timeout=0.05, write_timeout=0)
    ser.dtr = False
    ser.rts = False
    ser.reset_input_buffer()
    log, stop = [], threading.Event()
    th = threading.Thread(target=drain, args=(ser, log, stop))
    th.start()

    time.sleep(1.4)                     # baseline poll
    marks = []
    if action == "param":
        idx = int(args[0])
        marks.append((time.monotonic(), f"step {idx} up"))
        print("->", sirio_api.step_param(host, idx, True))
        time.sleep(1.4)
        marks.append((time.monotonic(), f"step {idx} down (restore)"))
        print("->", sirio_api.step_param(host, idx, False))
    elif action == "toggle":
        marks.append((time.monotonic(), "toggle"))
        print("->", sirio_api.toggle(host))
    time.sleep(1.4)

    stop.set()
    th.join()
    ser.close()

    frames, raw, times = frames_from(log)
    # baseline = signatures before the first action
    t0 = marks[0][0] if marks else time.monotonic()
    def sig(fr):
        reg = fr[2]
        return (fr[1], reg) if reg == 0x31 else (fr[1], reg, fr[3], fr[4], fr[5])
    baseline = {sig(fr) for t, fr in frames if t < t0 - 0.05}

    print(f"\n# {len(frames)} frames, baseline has {len(baseline)} signatures")
    print("# --- frames with a NEW signature (candidate commands) ---")
    for t, fr in frames:
        if sig(fr) not in baseline:
            near = min(marks, key=lambda m: abs(m[0] - t)) if marks else (t, "")
            dt = t - near[0]
            print(f"  t={t:8.3f}  {fr.hex(' ')}   [{near[1]} {dt:+.2f}s]")

    # raw window around each action, to catch non-standard framing
    for mt, name in marks:
        win = bytearray()
        for t, b in log:
            if mt - 0.3 <= t <= mt + 0.7:
                win += b
        print(f"\n# raw bytes around '{name}':\n  {win.hex(' ')}")


if __name__ == "__main__":
    main()
