"""Act as the J12 master from the PC via the FT232 - the reference implementation
we will port to the ESP32. Replaces the WiNET module: polls the ATmega for
telemetry and sends start/stop + setpoint commands.

WIRING (WiNET module UNPLUGGED, ATmega board powered from your bench supply):
  FT232 TX  -> J12.2 (ATmega RXD1)
  FT232 RX  -> J12.1 (ATmega TXD1)
  FT232 GND -> J12.4
  FT232 at 5 V logic.  Do NOT run this while the real module is plugged in
  (two masters would fight on J12.2).

  python sirio_master.py COM9 status
  python sirio_master.py COM9 monitor --interval 1
  python sirio_master.py COM9 toggle
  python sirio_master.py COM9 set-sp 50          # pressure setpoint -> 5.0 bar (raw 50)
  python sirio_master.py COM9 step 0 up 3        # param 0 +3 steps
"""
import argparse
import time

import serial

BAUD = 19200
# response reg -> how to pull fields out of (d0,d1,d2)
READS = [0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x35, 0x36, 0x37]


def build(op, d0=0, d1=0, d2=0):
    body = bytes([0xAA, 0xFF, op, d0, d1, d2])
    return body + bytes([sum(body) & 0xFF])


def xfer(ser, op, d0=0, d1=0, d2=0, timeout=0.25):
    """Send a request; return the matching 0xFE response frame, or None."""
    want = op + 0x10
    ser.reset_input_buffer()
    ser.write(build(op, d0, d1, d2))
    ser.flush()
    buf = bytearray()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        buf += ser.read(32)
        for i in range(len(buf) - 6):
            if (buf[i] == 0xAA and buf[i + 1] == 0xFE
                    and (sum(buf[i:i + 6]) & 0xFF) == buf[i + 6]
                    and buf[i + 2] == want):
                return bytes(buf[i:i + 7])
    return None


def send_cmd(ser, op, d0=0, d1=0, d2=0):
    """Fire a command (0x32/0x33/0x34); ATmega sends no 0xFE reply to these."""
    ser.write(build(op, d0, d1, d2))
    ser.flush()
    time.sleep(0.03)


def poll(ser):
    s, miss = {}, []
    for op in READS:
        fr = xfer(ser, op) or xfer(ser, op)  # one retry
        if not fr:
            miss.append(hex(op))
            continue
        d0, d1, d2 = fr[3], fr[4], fr[5]
        be = (d1 << 8) | d2
        if op == 0x11:
            s["setPointPress"] = d1
        elif op == 0x12:
            s["freqMax"] = d1
        elif op == 0x13:
            s["ampMax"], s["amp"] = d1, d2
        elif op == 0x14:
            s["r14"] = (d1, d2)
        elif op == 0x15:
            s["tempIgbt"], s["temp"] = d1, d2
        elif op == 0x16:
            s["flags"] = f"{d0:02x}{d1:02x}{d2:02x}"
        elif op == 0x35:
            s["hPowerOn"] = be
        elif op == 0x36:
            s["hPowerRunning"] = be
        elif op == 0x37:
            s["nrStarts"] = be
    if miss:
        s["_missing"] = ",".join(miss)
    return s


def read_param(ser, index):
    """Read current setpoint back via its status register (param 0 -> reg 0x21)."""
    if index != 0:
        return None  # other params aren't in the status poll; step blindly
    for _ in range(4):
        fr = xfer(ser, 0x11)
        if fr:
            return fr[4]
    return None


def set_setpoint(ser, target, max_steps=60):
    cur = read_param(ser, 0)
    if cur is None:
        print("cannot read setpoint")
        return
    print(f"setpoint {cur} -> {target}")
    steps = 0
    while cur != target and steps < max_steps:
        send_cmd(ser, 0x32 if target > cur else 0x33, d2=0)
        time.sleep(0.15)
        new = read_param(ser, 0)
        if new is None or new == cur:  # no movement -> stop (hit a limit)
            steps += 1
            if new == cur:
                print("no change, stopping")
                break
        cur = new if new is not None else cur
        steps += 1
    print("setpoint now", read_param(ser, 0))


def fmt(s):
    order = ["setPointPress", "press", "amp", "ampMax", "freq", "freqMax",
             "temp", "tempIgbt", "flags", "hPowerOn", "hPowerRunning", "nrStarts"]
    return "  ".join(f"{k}={s[k]}" for k in order if k in s) + \
        (f"   MISSING {s['_missing']}" if "_missing" in s else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    m = sub.add_parser("monitor")
    m.add_argument("--interval", type=float, default=1.0)
    sub.add_parser("toggle")
    sp = sub.add_parser("set-sp")
    sp.add_argument("value", type=int, help="setpoint raw (tenths of bar), e.g. 50 = 5.0")
    st = sub.add_parser("step")
    st.add_argument("index", type=int)
    st.add_argument("dir", choices=["up", "down"])
    st.add_argument("n", type=int, nargs="?", default=1)
    args = ap.parse_args()

    with serial.Serial(args.port, BAUD, timeout=0.05, write_timeout=1) as ser:
        ser.dtr = False
        ser.rts = False
        if args.cmd == "status":
            print(fmt(poll(ser)))
        elif args.cmd == "monitor":
            try:
                while True:
                    print(time.strftime("%H:%M:%S"), fmt(poll(ser)))
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                pass
        elif args.cmd == "toggle":
            send_cmd(ser, 0x34)
            time.sleep(0.3)
            print(fmt(poll(ser)))
        elif args.cmd == "set-sp":
            set_setpoint(ser, args.value)
        elif args.cmd == "step":
            for _ in range(args.n):
                send_cmd(ser, 0x32 if args.dir == "up" else 0x33, d2=args.index)
                time.sleep(0.15)
            print("done; status:", fmt(poll(ser)))


if __name__ == "__main__":
    main()
