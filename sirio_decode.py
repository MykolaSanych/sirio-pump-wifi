"""Locate known telemetry fields inside captured J12 frames.

Idea: the ATmega->module response carries values we already know from the HTTP
API (setPointPress, counters, volt, ...). Search each captured frame for those
integers in several byte encodings; multi-byte hits (counters, volt) pin down
the frame layout and endianness immediately. No running motor needed.

Workflow:
  1. python sirio_uart_scan.py COMx --baud <b> --frames   -> sirio_frames_*.txt
  2. python sirio_decode.py sirio_frames_123.txt --host 192.168.0.107
     (pulls live values from the module; or pass --values "setPointPress=47,...")

Input can also be raw hex:  python sirio_decode.py --hex "aa 05 2f 37 ..."
"""
import argparse
import json
import re

# Snapshot from 2026-11-12 in case the module is offline; --host refreshes it.
SNAPSHOT = {
    "temp": 26, "tempIgbt": 4, "amp": 155, "ampMax": 95, "volt": 375,
    "freq": 0, "freqMax": 55, "press": 0, "setPointPress": 47,
    "errorNumber": 0, "nrStarts": 18768, "hPowerOn": 10223, "hPowerRunning": 2147,
}
# Values this common produce too many false hits to be worth reporting as u8.
NOISY_U8 = {0, 1, 2, 3, 4, 255}


def load_live(host):
    import sirio_api  # same folder
    data = sirio_api.status(host)
    return {k: v for k, v in data.items() if isinstance(v, int)} if isinstance(data, dict) else {}


def parse_frames(path):
    """Return list of (label, bytes) from a sirio_frames_*.txt capture."""
    frames = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            i = line.find("len=")
            if i < 0:
                continue
            toks = line[i + 4:].split()
            if len(toks) < 2:
                continue
            try:
                frame = bytes(int(h, 16) for h in toks[1:] if re.fullmatch(r"[0-9a-fA-F]{2}", h))
            except ValueError:
                continue
            if frame:
                frames.append((f"line{ln}", frame))
    return frames


def encodings(v):
    """Yield (tag, pattern-bytes) for a value across plausible encodings."""
    out = []
    if 0 <= v <= 0xFF and v not in NOISY_U8:
        out.append(("u8", bytes([v])))
    if 0 <= v <= 0xFFFF:
        out.append(("u16le", v.to_bytes(2, "little")))
        out.append(("u16be", v.to_bytes(2, "big")))
    if 0 <= v <= 0xFFFFFFFF and v > 0xFFFF:
        out.append(("u32le", v.to_bytes(4, "little")))
        out.append(("u32be", v.to_bytes(4, "big")))
    return out


def find_all(hay, needle):
    offs, start = [], 0
    while True:
        j = hay.find(needle, start)
        if j < 0:
            return offs
        offs.append(j)
        start = j + 1


def width_rank(tag):
    return {"u8": 1, "u16le": 2, "u16be": 2, "u32le": 4, "u32be": 4}[tag]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames", nargs="?", help="sirio_frames_*.txt capture file")
    ap.add_argument("--hex", help="decode a single raw frame given as hex")
    ap.add_argument("--host", help="fetch current values live from the module")
    ap.add_argument("--values", help="override values: 'name=int,name=int'")
    args = ap.parse_args()

    values = dict(SNAPSHOT)
    if args.host:
        try:
            values.update(load_live(args.host))
            print(f"# live values from {args.host}")
        except Exception as exc:
            print(f"# live fetch failed ({exc}); using snapshot")
    if args.values:
        for pair in args.values.split(","):
            k, _, v = pair.partition("=")
            values[k.strip()] = int(v)

    if args.hex:
        frames = [("hex", bytes(int(h, 16) for h in args.hex.split()))]
    elif args.frames:
        frames = parse_frames(args.frames)
    else:
        ap.error("give a frames file or --hex")

    print(f"# {len(frames)} frame(s); searching {len(values)} known values\n")
    for label, frame in frames:
        hits = []
        for name, v in values.items():
            for tag, pat in encodings(v):
                for off in find_all(frame, pat):
                    hits.append((width_rank(tag), off, name, v, tag))
        if not hits:
            continue
        hits.sort(key=lambda h: (-h[0], h[1]))  # widest (most distinctive) first
        print(f"{label}  len={len(frame)}  {frame.hex(' ')}")
        for _, off, name, v, tag in hits:
            print(f"    @{off:<3} {tag:<6} {name}={v}")
        print()


if __name__ == "__main__":
    main()
