"""Minimal client for the Sirio WiNET module local HTTP API.

The module serves on port 80 (POST, application/x-www-form-urlencoded, no auth
for status). Two jobs:
  1. Verify that control actually works on our real unit.
  2. LABEL the J12 UART traffic: run `watch` (or fire `toggle`) here while
     `sirio_uart_scan.py --frames` captures the module<->ATmega line, then line
     up the wall-clock timestamps to see which bytes mean what.

Stdlib only (urllib) - no pip install needed. The login cookie persists for the
life of one process, so `login` then advanced reads/steps work in `watch`.

Examples:
  python sirio_api.py 192.168.10.1 status
  python sirio_api.py 192.168.10.1 watch --interval 1 --seconds 120
  python sirio_api.py 192.168.10.1 toggle              # start/stop (key=011)
  python sirio_api.py 192.168.10.1 getbase 22          # read base param 2.2
  python sirio_api.py 192.168.10.1 step 20 up          # base param 2.0 +1 step
  python sirio_api.py 192.168.10.1 login 1230
  python sirio_api.py 192.168.10.1 netstatus           # /ajax/get-status
"""
import argparse
import http.cookiejar
import json
import time
import urllib.parse
import urllib.request

# Fields we print in `watch`, when present. Raw tenths noted in WINET_API_NOTES.md.
WATCH_KEYS = [
    "press", "setPointPress", "amp", "ampMax", "freq", "freqMax", "volt",
    "temp", "tempIgbt", "motorOn", "standBy", "pumpMan", "errorActive",
    "errorNumber", "nrStarts",
]

_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)


def post(host, path, fields, timeout=5.0):
    """POST form-encoded; return parsed JSON if possible, else the raw text."""
    url = f"http://{host}{path}"
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with _opener.open(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def status(host):
    return post(host, "/ajax/get-registers", {"key": "001"})


def net_status(host):
    return post(host, "/ajax/get-status", {})


def get_param(host, index, advanced=False):
    key = "003" if advanced else "002"
    return post(host, "/ajax/get-registers", {"key": key, "index": str(index)})


def step_param(host, index, up, advanced=False):
    # base: 007 up / 008 down; advanced (after login): 009 up / 010 down.
    key = ("009" if up else "010") if advanced else ("007" if up else "008")
    return post(host, "/ajax/set-registers", {"key": key, "index": str(index)})


def toggle(host):
    return post(host, "/ajax/set-registers", {"key": "011"})


def login(host, pin):
    return post(host, "/ajax/login", {"pin": str(pin)})


def _compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _watch_line(obj):
    if isinstance(obj, dict):
        present = {k: obj[k] for k in WATCH_KEYS if k in obj}
        if present:
            return " ".join(f"{k}={present[k]}" for k in present)
    return _compact(obj)


def watch(host, interval, seconds, logfile, full):
    end = time.monotonic() + seconds if seconds else None
    f = open(logfile, "w", encoding="utf-8") if logfile else None
    print(f"# watching {host} every {interval}s"
          + (f" for {seconds}s" if seconds else "") + "  (Ctrl+C to stop)")
    try:
        while end is None or time.monotonic() < end:
            tick = time.monotonic()
            stamp = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
            try:
                data = status(host)
                line = f"{stamp}  " + (_compact(data) if full else _watch_line(data))
            except Exception as exc:  # keep polling across transient errors
                line = f"{stamp}  ERROR {exc}"
            print(line)
            if f:
                f.write(line + "\n")
                f.flush()
            sleep = interval - (time.monotonic() - tick)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        print("\n# stopped")
    finally:
        if f:
            f.close()
            print(f"# saved {logfile}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", help="module IP or hostname, e.g. 192.168.10.1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("netstatus")
    sub.add_parser("toggle")

    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=1.0)
    w.add_argument("--seconds", type=float, default=0, help="0 = until Ctrl+C")
    w.add_argument("--log", default=f"sirio_api_watch_{int(time.time())}.txt")
    w.add_argument("--full", action="store_true", help="dump the whole JSON each tick")

    g = sub.add_parser("getbase")
    g.add_argument("index", type=int)
    ga = sub.add_parser("getadv")
    ga.add_argument("index", type=int)

    s = sub.add_parser("step")
    s.add_argument("index", type=int)
    s.add_argument("dir", choices=["up", "down"])
    s.add_argument("--adv", action="store_true", help="advanced param (needs login first)")

    lo = sub.add_parser("login")
    lo.add_argument("pin")

    args = ap.parse_args()

    if args.cmd == "status":
        print(_compact(status(args.host)))
    elif args.cmd == "netstatus":
        print(_compact(net_status(args.host)))
    elif args.cmd == "toggle":
        print(_compact(toggle(args.host)))
    elif args.cmd == "watch":
        watch(args.host, args.interval, args.seconds, args.log, args.full)
    elif args.cmd == "getbase":
        print(_compact(get_param(args.host, args.index, advanced=False)))
    elif args.cmd == "getadv":
        print(_compact(get_param(args.host, args.index, advanced=True)))
    elif args.cmd == "step":
        print(_compact(step_param(args.host, args.index, args.dir == "up", advanced=args.adv)))
    elif args.cmd == "login":
        print(_compact(login(args.host, args.pin)))


if __name__ == "__main__":
    main()
