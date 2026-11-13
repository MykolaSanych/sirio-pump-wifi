// Власна прошивка Wi-Fi-модуля насоса Sirio (ESP8266, замість WiNET).
// Наша веб-морда (PROGMEM) + майстер протоколу J12 до ATmega.
// UART0 (Serial) = лінія J12 @19200 через перетворювач рівнів. У Serial НЕ друкувати.
//
// Ключове в протоколі: op 0x31 = ЧИТАННЯ БАЙТА ПАМ'ЯТІ ATmega за адресою (адреса в d2),
// відповідь reg 0x41, значення в d2. Параметри меню — за адресами 0..59, лічильники
// помилок e0..e13 — за адресами 0xDC..0xE9. Тобто параметри читаються НАЖИВО, без кешу.
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPUpdateServer.h>
#include <EEPROM.h>
#include "webui.h"

#ifndef ATMEGA_BAUD
#define ATMEGA_BAUD 19200
#endif
#define J12 Serial
static const char* FWVER = "1.8";
static const char* ADV_PIN_DEFAULT = "1230";
static const uint8_t ERR_BASE = 0xDC;     // e0..e13 = пам'ять 0xDC..0xE9

ESP8266WebServer server(80);
ESP8266HTTPUpdateServer httpUpdater;
static const char* AP_PASS = "siriopump";

// ---------------- стала конфігурація (EEPROM): лише мережа ----------------
struct Config {
  char magic[4];          // "SIR1"
  char ssid[33];
  char pass[65];
  uint8_t dhcp;           // 1 = авто, 0 = статичний
  uint32_t ip, mask, gw;
  char pin[9];
};
Config cfg;

// --- набори параметрів (профілі), до 5; у EEPROM після Config ---
#define SETS_N 5
struct ParamSet { uint8_t used; char name[25]; uint8_t p[60]; };
ParamSet sets[SETS_N];
const int EE_SETS = 128;   // Config < 128 байтів

void saveCfg() { EEPROM.put(0, cfg); EEPROM.commit(); }
void saveSets() {
  char sm[4] = {'S', 'E', 'T', '1'};
  EEPROM.put(EE_SETS, sm);
  for (int i = 0; i < SETS_N; i++) EEPROM.put(EE_SETS + 4 + i * sizeof(ParamSet), sets[i]);
  EEPROM.commit();
}
void loadCfg() {
  EEPROM.get(0, cfg);
  if (memcmp(cfg.magic, "SIR1", 4) != 0) {
    memset(&cfg, 0, sizeof(cfg));
    memcpy(cfg.magic, "SIR1", 4); cfg.dhcp = 1; strcpy(cfg.pin, ADV_PIN_DEFAULT);
    memset(sets, 0, sizeof(sets));
    saveCfg(); saveSets();
  } else {
    char sm[4]; EEPROM.get(EE_SETS, sm);
    if (memcmp(sm, "SET1", 4) != 0) { memset(sets, 0, sizeof(sets)); saveSets(); }   // сети ще не ініціалізовані
    else for (int i = 0; i < SETS_N; i++) {
      EEPROM.get(EE_SETS + 4 + i * sizeof(ParamSet), sets[i]);
      if (sets[i].used != 1) memset(&sets[i], 0, sizeof(ParamSet));
      else sets[i].name[sizeof(sets[i].name) - 1] = 0;
    }
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
// читання байта пам'яті ATmega за адресою (op 0x31 -> reg 0x41, значення в d2)
bool readMem(uint8_t addr, uint8_t* val) {
  uint8_t r[7];
  if (j12xfer(0x31, r, 0, 0, addr) || j12xfer(0x31, r, 0, 0, addr)) { *val = r[5]; return true; }
  return false;
}

uint8_t regv[0x48][3];
uint8_t elog[14];
uint8_t unitParam = 9;   // param 6 (одиниця): 9=BAR, 10=PSI
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
    if (!pollReg(op)) { if (first) atmegaOk = false; break; }
    if (first) { atmegaOk = true; first = false; }
  }
}
void pollSlow() {                      // журнал помилок + одиниця виміру
  uint8_t v;
  for (uint8_t i = 0; i < 14; i++) if (readMem(ERR_BASE + i, &v)) elog[i] = v;
  if (readMem(6, &v)) unitParam = v;
}
uint16_t be(uint8_t reg) { return ((uint16_t)regv[reg][1] << 8) | regv[reg][2]; }

// ---------------- /ajax обробники ----------------
void sendJson(const String& s) { server.send(200, "application/json", s); }

void handleStatus() {
  uint8_t fl0 = regv[0x26][0], fl2 = regv[0x26][2];
  String s = "{\"key\":1";
  s += ",\"standBy\":" + String((fl0 & 0x20) ? 1 : 0);
  s += ",\"motorOn\":" + String((fl0 & 0x80) ? 1 : 0);
  // errorActive поки false: fl2&0x20 виявився бітом "зупинено", а не аварії.
  // Активну аварію мапувати на РЕАЛЬНІЙ аварії (дивитись fl26 + peek), тоді ввімкнути 3-й стан кнопки.
  s += ",\"flagError\":0,\"errorActive\":false,\"errorNumber\":0";
  s += ",\"extEnabled\":" + String((fl2 & 0x02) ? 1 : 0);
  s += ",\"barOrPsi\":" + String(unitParam == 10 ? 1 : 0);
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
  s += ",\"fl26\":\"" + String(fl0, HEX) + "," + String(regv[0x26][1], HEX) + "," + String(fl2, HEX) + "\"";   // debug прапорців
  for (int i = 0; i < 14; i++) s += ",\"e" + String(i) + "\":" + String(elog[i]);
  s += "}";
  sendJson(s);
}

bool loggedIn = false;

void handleGetRegisters() {
  String key = server.arg("key");
  if (key == "001") { handleStatus(); return; }
  if (key == "002" || key == "003") {           // читання параметра НАЖИВО з пам'яті ATmega
    int idx = server.arg("index").toInt();
    if ((key == "003") && !loggedIn) { sendJson("{\"key\":3,\"result\":false}"); return; }
    uint8_t v;
    if (idx >= 0 && idx < 256 && readMem((uint8_t)idx, &v))
      sendJson("{\"key\":" + key.substring(2) + ",\"result\":true,\"parIndex\":" + String(idx) + ",\"value\":" + String(v) + "}");
    else
      sendJson("{\"key\":" + key.substring(2) + ",\"result\":false,\"parIndex\":" + String(idx) + "}");
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
    j12send(up ? 0x32 : 0x33, 0, 0, (uint8_t)idx);   // крок параметра; наступне читання покаже нове значення
    sendJson("{\"result\":true,\"parIndex\":" + String(idx) + "}");
    return;
  }
  sendJson("{\"result\":false}");
}

void handlePeek() {                 // налагодження: прочитати байт пам'яті за адресою
  int addr = server.arg("addr").toInt();
  uint8_t v;
  bool ok = (addr >= 0 && addr < 256 && readMem((uint8_t)addr, &v));
  sendJson("{\"addr\":" + String(addr) + ",\"ok\":" + (ok ? "true" : "false") + ",\"value\":" + (ok ? String(v) : String(-1)) + "}");
}

// ---------------- набори параметрів (сети) ----------------
void handleSets() {                 // GET: список сетів
  String s = "{\"sets\":[";
  for (int i = 0; i < SETS_N; i++) {
    if (i) s += ",";
    s += "{\"id\":" + String(i) + ",\"used\":" + (sets[i].used ? "true" : "false") + ",\"name\":\"" + String(sets[i].name) + "\"}";
  }
  s += "]}"; sendJson(s);
}
void handleSetCreate() {            // POST name= : новий сет = знімок живих параметрів
  int id = -1; for (int i = 0; i < SETS_N; i++) if (!sets[i].used) { id = i; break; }
  if (id < 0) { sendJson("{\"result\":false,\"error\":\"full\"}"); return; }
  memset(&sets[id], 0, sizeof(ParamSet));
  sets[id].used = 1;
  String nm = server.arg("name"); if (!nm.length()) nm = "Сет " + String(id + 1);
  strlcpy(sets[id].name, nm.c_str(), sizeof(sets[id].name));
  for (int i = 0; i < 60; i++) { uint8_t v = 0; readMem(i, &v); sets[id].p[i] = v; }
  saveSets();
  sendJson("{\"result\":true,\"id\":" + String(id) + "}");
}
void handleSetRename() {
  int id = server.arg("id").toInt();
  if (id < 0 || id >= SETS_N || !sets[id].used) { sendJson("{\"result\":false}"); return; }
  strlcpy(sets[id].name, server.arg("name").c_str(), sizeof(sets[id].name)); saveSets();
  sendJson("{\"result\":true}");
}
void handleSetDelete() {
  int id = server.arg("id").toInt();
  if (id < 0 || id >= SETS_N) { sendJson("{\"result\":false}"); return; }
  sets[id].used = 0; saveSets(); sendJson("{\"result\":true}");
}
void handleSetRead() {              // GET id= : усі 60 параметрів сету
  int id = server.arg("id").toInt();
  if (id < 0 || id >= SETS_N || !sets[id].used) { sendJson("{\"result\":false}"); return; }
  String s = "{\"result\":true,\"id\":" + String(id) + ",\"name\":\"" + String(sets[id].name) + "\",\"params\":[";
  for (int i = 0; i < 60; i++) { if (i) s += ","; s += String(sets[id].p[i]); }
  s += "]}"; sendJson(s);
}
void handleSetWrite() {             // POST id= index= value= : офлайн-редагування сету
  int id = server.arg("id").toInt(), idx = server.arg("index").toInt(), val = server.arg("value").toInt();
  if (id < 0 || id >= SETS_N || !sets[id].used || idx < 0 || idx >= 60) { sendJson("{\"result\":false}"); return; }
  sets[id].p[idx] = (uint8_t)constrain(val, 0, 255); saveSets();
  sendJson("{\"result\":true}");
}
void handleSetApply() {             // POST id= : застосувати сет до частотника (лише у стоп-режимі)
  int id = server.arg("id").toInt();
  if (id < 0 || id >= SETS_N || !sets[id].used) { sendJson("{\"result\":false}"); return; }
  if (!(regv[0x26][0] & 0x20)) { sendJson("{\"result\":false,\"error\":\"running\"}"); return; }  // standBy потрібен
  int changed = 0, totalSteps = 0;
  for (int i = 0; i < 60 && totalSteps < 700; i++) {
    uint8_t cur; if (!readMem(i, &cur)) continue;
    uint8_t tgt = sets[id].p[i];
    int guard = 0; bool moved = false;
    while (cur != tgt && guard < 120 && totalSteps < 700) {
      j12send(cur < tgt ? 0x32 : 0x33, 0, 0, (uint8_t)i); delay(60); totalSteps++; guard++;
      uint8_t nv; if (!readMem(i, &nv) || nv == cur) break;   // уперлись у межу — припиняємо
      cur = nv; moved = true;
    }
    if (moved) changed++;
  }
  sendJson("{\"result\":true,\"changed\":" + String(changed) + ",\"steps\":" + String(totalSteps) + "}");
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
  saveCfg();
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
    saveCfg(); applyWifi();
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
  EEPROM.begin(1024);
  loadCfg();
  J12.begin(ATMEGA_BAUD);
  memset(regv, 0, sizeof(regv)); memset(elog, 0, sizeof(elog));

  WiFi.persistent(false);
  String ap = "Sirio-" + String(ESP.getChipId(), HEX);
  WiFi.softAP(ap.c_str(), AP_PASS);
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
  server.on("/ajax/peek", handlePeek);
  server.on("/ajax/sets", handleSets);
  server.on("/ajax/set-create", handleSetCreate);
  server.on("/ajax/set-rename", handleSetRename);
  server.on("/ajax/set-delete", handleSetDelete);
  server.on("/ajax/set-read", handleSetRead);
  server.on("/ajax/set-write", handleSetWrite);
  server.on("/ajax/set-apply", handleSetApply);
  httpUpdater.setup(&server, "/update", "admin", cfg.pin);
  server.begin();
}

uint32_t lastPoll = 0, lastSlow = 0;
void loop() {
  server.handleClient();
  uint32_t now = millis();
  if (now - lastPoll >= 500) { lastPoll = now; pollStatus(); }
  if (atmegaOk && now - lastSlow >= 5000) { lastSlow = now; pollSlow(); }
}
