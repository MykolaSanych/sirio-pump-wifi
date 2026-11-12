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
| **0x31** | **read one byte of ATmega memory at address `d2`** | d2 = address |
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
| 0x41 | 0x31 | **the memory byte at the requested address, in `d2`** |
| 0x45 | 0x35 | hPowerOn (u16 **big-endian**, d1 d2) |
| 0x46 | 0x36 | hPowerRunning (u16 BE) |
| 0x47 | 0x37 | nrStarts (u16 BE) |

16-bit values are **big-endian**. `volt`, `press`, `freq` are not among the 10
status registers; they most likely live at some ATmega memory addresses readable
via op 0x31 — map them with a live (running) pump, when they are non-zero.

## Memory read (op 0x31) — the real way to read parameters

Op 0x31 reads **any single byte of the ATmega's config/EEPROM memory** by address
(`AA FF 31 00 00 <addr> <csum>` → `AA FE 41 00 <?> <value> <csum>`, value in `d2`).
This is how the module reads menu parameters and error counters — there is no
separate "read parameter N" command. Verified live, the memory map is:

| addresses | meaning |
|-----------|---------|
| **0x00..0x3B** (0..59) | menu parameters, indexed by menu code without the dot (0.0→0, 2.1→21, 5.9→59) |
| 0x80..0xBB | a second (working/backup) copy of the parameters |
| 0x78..0x7B | some live values (0x7A = tempIgbt) |
| **0xDC..0xE9** | error counters e0..e13 (how many times each alarm occurred) |
| **0xF0..0xF5** | hPowerOn, hPowerRunning, nrStarts (16-bit little-endian) |

`volt`, `freq`, `press` and the *active* alarm number are runtime values the
inverter produces only when it is mains-powered and actually pumping / in alarm;
they are not static data in the firmware, so they must be mapped on a live unit
(read the 0x21..0x26 status registers, or peek memory, while the pump runs/faults).
The WiNET firmware's own error-text table (E0 "Low supply voltage", E1 "High supply
voltage", E2 "Engine short circuit", E3 "Dry running", E4 "High ambient temperature",
E5 "IGBT module temperature high", E6 overload, E9 "Exceeding the limit pressure",
E10 "External error", E11 "Exceeding maximum hourly departures", E12 "12 volt error",
E13 pressure sensor) confirms the alarm numbering.

So parameters are **read live** from the inverter (not cached). Example confirmed
values: addr 0 = setpoint, addr 10 = ampMax (95 = 9.5 A), addr 21 = command source
(20 = PRESS), addr 22 = aux contact (23 = "1<->"); 255 = parameter disabled.

## What a replacement device (our ESP firmware) does, as the new master
1. Drives J12.2 (ATmega RX), listens on J12.1 (ATmega TX). Level-shift 5 V <-> 3.3 V
   (the WiNET carrier board already has this shifter between the ESP and J12).
2. Polls 0x11..0x16, 0x35..0x37 (~2 Hz) for live telemetry; parses 0xFE frames.
3. Reads parameters live via op 0x31 (addr 0..59) and error counters (addr 0xDC..0xE9).
4. Control: start/stop = 0x34; set a parameter = step it with 0x32/0x33 to the target.
