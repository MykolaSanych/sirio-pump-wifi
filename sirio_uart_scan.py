"""Listen-only UART scan for the Sirio display board. Never writes to the port.

Scan all baud rates:  python sirio_uart_scan.py COM5
Log one baud rate:    python sirio_uart_scan.py COM5 --baud 19200 --seconds 60
Split into frames:    python sirio_uart_scan.py COM5 --baud 19200 --frames --gap 20
List ports:           python sirio_uart_scan.py --list
"""
import argparse
import time

import serial
from serial.tools import list_ports

BAUDS = [2400, 4800, 9600, 19200, 38400, 57600, 76800, 115200, 250000]
PARITY = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}


def capture(port, baud, parity, seconds):
    with serial.Serial(port, baud, parity=PARITY[parity], timeout=0.1, write_timeout=0) as ser:
        # Keep the adapter's control lines from driving anything.
        ser.dtr = False
        ser.rts = False
        ser.reset_input_buffer()
        data = bytearray()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            data += ser.read(4096)
    return bytes(data)


def capture_frames(port, baud, parity, seconds, gap_ms):
    """Group bytes into frames split by idle gaps; USB latency makes gaps approximate."""
    frames = []
    with serial.Serial(port, baud, parity=PARITY[parity], timeout=0.002, write_timeout=0) as ser:
        ser.dtr = False
        ser.rts = False
        ser.reset_input_buffer()
        start = time.monotonic()
        end = start + seconds
        frame, frame_t, last_t = bytearray(), 0.0, 0.0
        while time.monotonic() < end:
            chunk = ser.read(4096)
            now = time.monotonic() - start
            if chunk:
                if frame and (now - last_t) * 1000 > gap_ms:
                    frames.append((frame_t, bytes(frame)))
                    frame = bytearray()
                if not frame:
                    frame_t = now
                frame += chunk
                last_t = now
        if frame:
            frames.append((frame_t, bytes(frame)))
    return frames


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--parity", choices=PARITY, default="N")
    ap.add_argument("--seconds", type=float, default=4)
    ap.add_argument("--frames", action="store_true", help="with --baud: print one line per frame")
    ap.add_argument("--gap", type=float, default=20, help="idle ms that ends a frame (default 20)")
    args = ap.parse_args()

    if args.list or not args.port:
        for p in list_ports.comports():
            print(p.device, "-", p.description)
        return

    if args.frames:
        if not args.baud:
            ap.error("--frames needs --baud")
        frames = capture_frames(args.port, args.baud, args.parity, args.seconds, args.gap)
        name = f"sirio_frames_{args.baud}_{int(time.time())}.txt"
        with open(name, "w") as f:
            prev = None
            for t, data in frames:
                dt = "" if prev is None else f"+{(t - prev) * 1000:.0f}ms"
                line = f"{t:9.3f} {dt:>9} len={len(data):<4} {data.hex(' ')}"
                print(line)
                f.write(line + "\n")
                prev = t
        print(f"{len(frames)} frames, saved {name}")
        return

    for baud in [args.baud] if args.baud else BAUDS:
        data = capture(args.port, baud, args.parity, args.seconds)
        print(f"{baud:>7} 8{args.parity}1: {len(data):>6} bytes, {len(set(data)):>3} distinct | {data[:48].hex(' ')}")
        if args.baud and data:
            name = f"sirio_{baud}_{int(time.time())}.bin"
            with open(name, "wb") as f:
                f.write(data)
            print("saved", name)


if __name__ == "__main__":
    main()
