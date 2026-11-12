"""Capture the J12 "read parameter N" command: sniff J12.2 while triggering HTTP
getbase for several indices, then report frames that are not the steady poll.
  python sirio_capture_read.py COM9 192.168.0.107 2 5 16 27
"""
import sys, threading, time
import serial, sirio_api

port, host = sys.argv[1], sys.argv[2]
indices = [int(x) for x in sys.argv[3:]] or [2, 5, 16, 27]

ser = serial.Serial(port, 19200, timeout=0.05, write_timeout=0)
ser.dtr = False; ser.rts = False; ser.reset_input_buffer()
log = []; stop = threading.Event()
def drain():
    while not stop.is_set():
        b = ser.read(256)
        if b: log.append((time.monotonic(), b))
th = threading.Thread(target=drain); th.start()

time.sleep(1.4)
marks = []
for idx in indices:
    marks.append((time.monotonic(), "getbase %d" % idx, idx))
    try: print("->", idx, sirio_api.get_param(host, idx))
    except Exception as e: print("err", idx, e)
    time.sleep(0.9)
time.sleep(0.6)
stop.set(); th.join(); ser.close()

data = bytearray(); times = []
for t, b in log:
    for _ in b: times.append(t)
    data += b
frames = []; i = 0
while i < len(data) - 6:
    if data[i] == 0xAA and (sum(data[i:i+6]) & 0xFF) == data[i+6]:
        frames.append((times[i], bytes(data[i:i+7]))); i += 7
    else: i += 1
t0 = marks[0][0]
def sig(fr):
    reg = fr[2]
    return (fr[1], reg) if reg == 0x31 else (fr[1], reg, fr[3], fr[4], fr[5])
baseline = {sig(fr) for t, fr in frames if t < t0 - 0.05}
print("\n# %d frames, baseline %d sigs" % (len(frames), len(baseline)))
print("# NEW frames near each getbase:")
for t, fr in frames:
    if sig(fr) not in baseline:
        near = min(marks, key=lambda m: abs(m[0]-t))
        print("  t=%.3f  %s   [%s +%.2fs]" % (t, fr.hex(' '), near[1], t - near[0]))
