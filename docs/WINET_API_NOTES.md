# Sirio Wi-Fi module (WiNET): what is known

Collected 2026-11-02 from public Home Assistant integrations. Not yet verified on our unit.

## Hardware
- Module: ACG.MD.WIFI.SR5V (declaration: KIT.MD.WIFI.SR5V), made by Net Software (WiNET).
- Inside: Espressif ESP-WROOM-02 (ESP8266), 2x3 female header, two LEDs.
- Plugs into J12 on the display board (IN10_100314 revB): 1 TXD1, 2 RXD1, 3 +5 V, 4 GND, 5-6 unknown.
- ESP8266 has no flash encryption, so the module firmware can be dumped with esptool
  (this would give the J12 UART protocol).

## Local web API (module on LAN, port 80, no auth for status)
All POST, application/x-www-form-urlencoded.

| Request | Meaning |
|---|---|
| /ajax/get-registers key=001 | status JSON |
| /ajax/get-registers key=002&index=N | read base parameter N |
| /ajax/get-registers key=003&index=N | read advanced parameter N (login) |
| /ajax/set-registers key=007 / 008 &index=N | base parameter N one step up / down |
| /ajax/set-registers key=009 / 010 &index=N | advanced parameter step (login) |
| /ajax/set-registers key=011 | toggle run / stand-by |
| /ajax/login pin=... | advanced login (bolkonskiy/sirio-ha uses pin 1230) |
| /ajax/get-status | network / firmware status |

Pages: /management.html, /status.html, /user.html.

Status fields: press, setPointPress, amp, ampMax (tenths), temp, tempIgbt (C), volt (V),
freq, freqMax (Hz), standBy, flagError, master, doubleSetPoint, extEnabled, extErr, motorOn,
pilota, pumpMan (0/1), errorActive, errorNumber, e0..e13, hPowerOn, hPowerRunning, nrStarts.

Parameter index = menu code without the dot (0.0 -> 0, 2.2 -> 22, 4.0 -> 40).
Raw codes: on/off 7/8; aux contact (22): 23 "1<->", 24 "2<-", 25 "3 X2"; command source (21):
18 MAN, 19 0-10V, 20 PRESS. Pressures raw/10 bar, Imax raw/10 A.
Parameter read ~1.7 s each, status ~0.1 s. Never overlap requests.

## Sources
- https://github.com/guffy1234/ha-italtecnica-sirio (CLAUDE.md has the register map)
- https://github.com/RobinCK/ha-sirio-winet (WiNET firmware 0.17, Sirio Universal)
- https://github.com/bolkonskiy/sirio-ha
- https://github.com/orgs/home-assistant/discussions/3336
