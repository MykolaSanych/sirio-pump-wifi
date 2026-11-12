// Власна прошивка Wi-Fi-модуля насоса Sirio (ESP8266, замість WiNET).
// Наша веб-морда (PROGMEM) + майстер протоколу J12 до ATmega.
// UART0 (Serial) = лінія J12 @19200 через перетворювач рівнів. У Serial НЕ друкувати.
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPUpdateServer.h>
#include <EEPROM.h>
#include "webui.h"

#ifndef ATMEGA_BAUD
#define ATMEGA_BAUD 19200
#endif
#define J12 Serial
static const char* FWVER = "1.0";
static const char* ADV_PIN_DEFAULT = "1230";

ESP8266WebServer server(80);
ESP8266HTTPUpdateServer httpUpdater;      // OTA через браузер: /update
static const char* AP_PASS = "siriopump"; // пароль точки доступу (WPA2, 8+ символів)

// ---------------- стала конфігурація (EEPROM) ----------------
struct Config {
  char magic[4];          // "SIR1"
  char ssid[33];
  char pass[65];
  uint8_t dhcp;           // 1 = авто, 0 = статичний
  uint32_t ip, mask, gw;  // статичні (мережевий порядок)
  char pin[9];            // PIN для розширених
};
Config cfg;
uint8_t params[60];       // кеш параметрів (як у WiNET)

// реальні значення з апарата користувача — початковий кеш (255 = вимкнений)
static const uint8_t PARAM_SEED[60] = {
  47,12,7,70,33,5,9,255,255,255,   // 0.x
  95,21,25,38,50,10,15,7,1,255,     // 1.x
  8,20,23,27,3,30,5,5,7,0,          // 2.x
  0,0,0,0,0,0,0,0,0,0,              // 3.x (сервісні, не в UI)
  0,0,11,8,0,10,0,0,1,0,            // 4.x
  0,0,0,0,0,0,14,189,0,0            // 5.x
};
const int EE_PARAMS = sizeof(Config);
const int EE_SIZE = sizeof(Config) + 60 + 8;

void saveAll() {
  EEPROM.put(0, cfg);
  for (int i = 0; i < 60; i++) EEPROM.write(EE_PARAMS + i, params[i]);
  EEPROM.commit();
}
void loadAll() {
  EEPROM.get(0, cfg);
  if (memcmp(cfg.magic, "SIR1", 4) != 0) {
    memset(&cfg, 0, sizeof(cfg));
    memcpy(cfg.magic, "SIR1", 4); cfg.dhcp = 1; strcpy(cfg.pin, ADV_PIN_DEFAULT);
    memcpy(params, PARAM_SEED, 60);
    saveAll();
  } else {
    for (int i = 0; i < 60; i++) params[i] = EEPROM.read(EE_PARAMS + i);
  }
}

// ---------------- майстер J12 ----------------
uint8_t j12cks(const uint8_t* b) { uint16_t s = 0; for (int i = 0; i < 6; i++) s += b[i]; return s & 0xFF; }
void j12send(uint8_t op, uint8_t d0 = 0, uint8_t d1 = 0, uint8_t d2 = 0) {
  uint8_t b[7] = { 0xAA, 0xFF, op, d0, d1, d2, 0 }; b[6] = j12cks(b);
  J12.write(b, 7); J12.flush();
}
// надсилає запит, чекає кадр AA FE з reg == op+0x10
bool j12xfer(uint8_t op, uint8_t* resp, uint8_t d0 = 0, uint8_t d1 = 0, uint8_t d2 = 0, uint16_t to = 160) {
  while (J12.available()) J12.read();
  j12send(op, d0, d1, d2);
  uint8_t buf[48]; int n = 0; uint32_t end = millis() + to; uint8_t want = op + 0x10;
  while ((int32_t)(millis() - end) < 0) {
    while (J12.available()) {
      uint8_t c = J12.read();
      if (n == 48) { memmove(buf, buf + 1, 47); n = 47; }
      buf[n++] = c;
      for (int i = 0; i + 7 <= n; i++) {
        if (buf[i] == 0xAA && buf[i + 1] == 0xFE) {
          uint16_t s = 0; for (int k = 0; k < 6; k++) s += buf[i + k];
          if ((s & 0xFF) == buf[i + 6] && buf[i + 2] == want) { memcpy(resp, buf + i, 7); return true; }
        }
      }
    }
    yield();
  }
  return false;
}

uint8_t regv[0x48][3];
uint8_t elog[14];

bool atmegaOk = false;
bool pollReg(uint8_t op) {
  uint8_t r[7];
  if (j12xfer(op, r) || j12xfer(op, r)) {
    uint8_t reg = r[2]; if (reg < 0x48) { regv[reg][0] = r[3]; regv[reg][1] = r[4]; regv[reg][2] = r[5]; }
    return true;
  }
  return false;
}
void pollStatus() {
  const uint8_t ops[] = { 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x35, 0x36, 0x37 };
  bool first = true;
  for (uint8_t op : ops) {
    if (!pollReg(op)) { if (first) atmegaOk = false; break; }  // ATmega відсутній — не марнуємо час
    if (first) { atmegaOk = true; first = false; }
  }
}
void pollErrlog() {               // best-effort: op 0x31 з курсором 0..13 -> reg 0x41 d2
  for (uint8_t i = 0; i < 14; i++) {
    uint8_t r[7];
    if (j12xfer(0x31, r, 0, 0, i)) elog[i] = r[5];
  }
}
uint16_t be(uint8_t reg) { return ((uint16_t)regv[reg][1] << 8) | regv[reg][2]; }

// ---------------- /ajax обробники ----------------
void sendJson(const String& s) { server.send(200, "application/json", s); }

void handleStatus() {
  uint8_t fl0 = regv[0x26][0], fl2 = regv[0x26][2];
  int standBy = (fl0 & 0x20) ? 1 : 0;
  int motorOn = (fl0 & 0x80) ? 1 : 0;
  int extEn   = (fl2 & 0x02) ? 1 : 0;
  String s = "{\"key\":1";
  s += ",\"standBy\":" + String(standBy);
  s += ",\"motorOn\":" + String(motorOn);
  s += ",\"flagError\":0,\"errorActive\":false,\"errorNumber\":0";
  s += ",\"extEnabled\":" + String(extEn);
  s += ",\"barOrPsi\":" + String(params[6] == 10 ? 1 : 0);
  s += ",\"setPointPress\":" + String(regv[0x21][1]);
  s += ",\"freqMax\":" + String(regv[0x22][1]);
  s += ",\"ampMax\":" + String(regv[0x23][1]);
  s += ",\"amp\":" + String(regv[0x23][2]);
  s += ",\"tempIgbt\":" + String(regv[0x25][1]);
  s += ",\"temp\":" + String(regv[0x25][2]);
  s += ",\"press\":0,\"freq\":0,\"volt\":0";   // TODO: прив'язати на робочому насосі
  s += ",\"hPowerOn\":" + String(be(0x45));
  s += ",\"hPowerRunning\":" + String(be(0x46));
  s += ",\"nrStarts\":" + String(be(0x47));
  for (int i = 0; i < 14; i++) s += ",\"e" + String(i) + "\":" + String(elog[i]);
  s += "}";
  sendJson(s);
}

bool loggedIn = false;

void handleGetRegisters() {
  String key = server.arg("key");
  if (key == "001") { handleStatus(); return; }
  if (key == "002" || key == "003") {
    int idx = server.arg("index").toInt();
    bool adv = (key == "003");
    if (adv && !loggedIn) { sendJson("{\"key\":3,\"result\":false}"); return; }
    int val;
    if (idx == 0) val = regv[0x21][1];
    else if (idx == 10) val = regv[0x23][1];
    else if (idx >= 0 && idx < 60) val = params[idx];
    else { sendJson("{\"result\":false}"); return; }
    sendJson("{\"key\":" + key.substring(2) + ",\"result\":true,\"parIndex\":" + String(idx) + ",\"value\":" + String(val) + "}");
    return;
  }
  sendJson("{\"result\":false}");
}

void handleSetRegisters() {
  String key = server.arg("key");
  if (key == "011") { j12send(0x34); sendJson("{\"key\":11,\"result\":true}"); return; }
  bool up = (key == "007" || key == "009");
  bool down = (key == "008" || key == "010");
  bool adv = (key == "009" || key == "010");
  if (up || down) {
    if (adv && !loggedIn) { sendJson("{\"result\":false}"); return; }
    int idx = server.arg("index").toInt();
    if (idx < 0 || idx >= 60) { sendJson("{\"result\":false}"); return; }
    j12send(up ? 0x32 : 0x33, 0, 0, (uint8_t)idx);
    if (idx != 0 && idx != 10) {
      int v = params[idx] + (up ? 1 : -1);
      params[idx] = (uint8_t)constrain(v, 0, 255);
      saveAll();
    }
    sendJson("{\"result\":true,\"parIndex\":" + String(idx) + "}");
    return;
  }
  sendJson("{\"result\":false}");
}

void handleLogin() {
  loggedIn = (server.arg("pin") == cfg.pin);
  sendJson(String("{\"logged\":") + (loggedIn ? "true" : "false") + "}");
}

String encStr(uint8_t e) { return (e == ENC_TYPE_NONE) ? "NONE" : "WPA2_PSK"; }

void handleGetNetworks() {
  int n = WiFi.scanNetworks();
  String s = "{\"result\":true,\"connected\":";
  s += (WiFi.status() == WL_CONNECTED) ? "true" : "false";
  s += ",\"network\":\"" + (WiFi.isConnected() ? WiFi.SSID() : String("")) + "\"";
  s += ",\"available\":{\"network\":[";
  for (int i = 0; i < n; i++) {
    if (i) s += ",";
    s += "[false,\"" + String(i + 1) + "\",\"" + WiFi.SSID(i) + "\",\"" + String(WiFi.RSSI(i)) + "\",\"" + encStr(WiFi.encryptionType(i)) + "\"]";
  }
  s += "]}}";
  WiFi.scanDelete();
  sendJson(s);
}

void applyWifi() {
  WiFi.mode(WIFI_AP_STA);
  if (!cfg.dhcp && cfg.ip) WiFi.config(IPAddress(cfg.ip), IPAddress(cfg.gw), IPAddress(cfg.mask));
  else WiFi.config(0U, 0U, 0U);
  if (cfg.ssid[0]) WiFi.begin(cfg.ssid, cfg.pass);
}

void handleConnect() {
  strlcpy(cfg.ssid, server.arg("network").c_str(), sizeof(cfg.ssid));
  strlcpy(cfg.pass, server.arg("password").c_str(), sizeof(cfg.pass));
  saveAll();
  applyWifi();
  uint32_t end = millis() + 8000;
  while (WiFi.status() != WL_CONNECTED && (int32_t)(millis() - end) < 0) { delay(100); yield(); }
  sendJson(String("{\"connected\":") + (WiFi.status() == WL_CONNECTED ? "true" : "false") + "}");
}

void handleGetDhcp() {
  if (server.method() == HTTP_POST && server.hasArg("dhcpEnabled")) {
    cfg.dhcp = server.arg("dhcpEnabled").toInt() ? 1 : 0;
    IPAddress ip, mask, gw;
    ip.fromString(server.arg("staticIp")); mask.fromString(server.arg("staticMask")); gw.fromString(server.arg("staticGw"));
    cfg.ip = ip.v4(); cfg.mask = mask.v4(); cfg.gw = gw.v4();
    saveAll(); applyWifi();
  }
  IPAddress ip(cfg.ip), mask(cfg.mask), gw(cfg.gw);
  String s = "{\"dhcpEnabled\":" + String(cfg.dhcp);
  s += ",\"staticIp\":\"" + (cfg.ip ? ip.toString() : "0.0.0.0") + "\"";
  s += ",\"staticMask\":\"" + (cfg.mask ? mask.toString() : "0.0.0.0") + "\"";
  s += ",\"staticGw\":\"" + (cfg.gw ? gw.toString() : "0.0.0.0") + "\"}";
  sendJson(s);
}

void handleGetStatusNet() {
  bool conn = WiFi.status() == WL_CONNECTED;
  String s = "{\"status\":5,\"apConnected\":0,\"connected\":" + String(conn ? "true" : "false");
  s += ",\"network\":\"" + (conn ? WiFi.SSID() : String("")) + "\"";
  s += ",\"rssi\":" + String(conn ? WiFi.RSSI() : 0);
  s += ",\"currentIp\":\"" + WiFi.localIP().toString() + "\"";
  s += ",\"currentGw\":\"" + WiFi.gatewayIP().toString() + "\"";
  s += ",\"currentMask\":\"" + WiFi.subnetMask().toString() + "\"";
  s += ",\"fwVer\":\"" + String(FWVER) + "\"}";
  sendJson(s);
}

void handleRoot() {
  server.sendHeader("Content-Encoding", "gzip");
  server.send_P(200, "text/html", (PGM_P)INDEX_HTML_GZ, INDEX_HTML_GZ_LEN);
}

void setup() {
  EEPROM.begin(256);
  loadAll();
  J12.begin(ATMEGA_BAUD);
  memset(regv, 0, sizeof(regv)); memset(elog, 0, sizeof(elog));

  WiFi.persistent(false);
  String ap = "Sirio-" + String(ESP.getChipId(), HEX);
  WiFi.softAP(ap.c_str(), AP_PASS);       // резервна точка доступу завжди активна
  applyWifi();

  server.on("/", handleRoot);
  server.on("/index.html", handleRoot);
  server.on("/ajax/get-registers", handleGetRegisters);
  server.on("/ajax/set-registers", handleSetRegisters);
  server.on("/ajax/login", handleLogin);
  server.on("/ajax/get-networks", handleGetNetworks);
  server.on("/ajax/connect", handleConnect);
  server.on("/ajax/get-dhcp", handleGetDhcp);
  server.on("/ajax/get-status", handleGetStatusNet);
  httpUpdater.setup(&server, "/update", "admin", cfg.pin);   // OTA: браузер /update, логін admin / PIN
  server.begin();
}

uint32_t lastPoll = 0, lastErr = 0;
void loop() {
  server.handleClient();
  uint32_t now = millis();
  if (now - lastPoll >= 500) { lastPoll = now; pollStatus(); }
  if (atmegaOk && now - lastErr >= 5000) { lastErr = now; pollErrlog(); }
}
