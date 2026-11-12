# Sirio J12 UART protocol (reverse-engineered 2026-11-12)

Link between the WiNET Wi-Fi module and the display-board ATmega324PA, on J12
(ATmega USART1). Fully derived by sniffing with an FT232 and correlating with
the module's HTTP API on a bench unit (power stage dead, logic powered alone).

## Physical
- **19200 baud, 8N1**, 5 V logic.
- J12.1 = ATmega TXD1 (ATmega -> module). J12.2 = ATmega RXD1 (module -> ATmega).
- J12.3 = +5 V, J12.4 = GND. Module is the **master**; ATmega only answers.

## Frame (fixed 7 bytes, both directions)
```
AA  <dir>  <op/reg>  <d0>  <d1>  <d2>  <csum>
csum = (AA + dir + op + d0 + d1 + d2) & 0xFF      ; simple additive sum
dir = 0xFF  master -> ATmega  (requests / commands)
dir = 0xFE  ATmega -> master  (responses)
```
A response's reg = the request's op + 0x10 (0x11->0x21, 0x35->0x45, 0x31->0x41).

## Requests the module sends (dir 0xFF), ~2 Hz loop
| op | meaning | payload |
|----|---------|---------|
| 0x11..0x16 | read status register | 00 00 00 |
| 0x31 | read error-log entry | d2 = cursor (walks the e0..e13 buffer) |
| 0x35..0x37 | read status register | 00 00 00 |
| **0x32** | **parameter[d2] += 1** (step up) | d2 = param index |
| **0x33** | **parameter[d2] -= 1** (step down) | d2 = param index |
| **0x34** | **toggle run / stand-by** (start/stop) | 00 00 00 |

Commands (0x32/0x33/0x34) share the 0xFF header; they are just ops the ATmega
acts on. Setting a parameter = send 0x32/0x33 repeatedly until the read-back
value matches (same as pressing +/- on the panel).

## Responses (dir 0xFE) — field map
| resp reg | (from op) | fields |
|----------|-----------|--------|
| 0x21 | 0x11 | d1 = setPointPress (raw/10 bar) |
| 0x22 | 0x12 | d1 = freqMax (Hz) |
| 0x23 | 0x13 | d1 = ampMax (raw/10 A), d2 = amp (raw/10 A) |
| 0x24 | 0x14 | d1 = 04 (tempIgbt?), d2 = ? (121, unidentified) |
| 0x25 | 0x15 | d1 = tempIgbt (C), d2 = temp (C) |
| 0x26 | 0x16 | status flags, bit-packed (standBy / extEnabled / ... not yet split) |
| 0x41 | 0x31 | error-log entry selected by the request cursor (e0..e13) |
| 0x45 | 0x35 | hPowerOn (u16 **big-endian**, d1 d2) |
| 0x46 | 0x36 | hPowerRunning (u16 BE) |
| 0x47 | 0x37 | nrStarts (u16 BE) |

16-bit values are **big-endian**. Not yet located in the stream: `volt`, `press`,
`freq` (press/freq were 0 with the pump stopped; volt=366 never appears as-is -
likely a value the module derives, or a dead-stage phantom). Map them on a live
pump, when they are non-zero.

## Parameter map (base params, HTTP key=002; menu code = index with a dot)
| index | menu | meaning | value seen |
|-------|------|---------|------------|
| **0** | 0.0 | **pressure setpoint** | 47 = 4.7 bar |
| 10 | 1.0 | max current (ampMax) | 95 = 9.5 A |
| 21 | 2.1 | command source | 20 = PRESS |
| 22 | 2.2 | auxiliary contact mode | 23 = "1<->" |
(255 = parameter disabled / absent.)

## What our own device (ESP32) must do, as the new master
1. Drive J12.2 (ATmega RX), listen on J12.1 (ATmega TX), with the WiNET module
   removed. Level-shift 5 V <-> 3.3 V on both lines.
2. Poll 0x11..0x16, 0x35..0x37 (and 0x31 for errors) at ~2 Hz; parse 0xFE frames.
3. Control: start/stop = 0x34; set pressure = step param 0 (0x32/0x33) to target.
