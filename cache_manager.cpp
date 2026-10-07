// cache_manager.cpp
#include "cache_manager.h"
#include "config.h"
#include <SPIFFS.h>

bool cacheInit() {
  if (!SPIFFS.begin(true)) {
    Serial.println("[CACHE] SPIFFS mount failed");
    return false;
  }
  return true;
}

// ---- Users binary format: count(2) + records ----
// แต่ละ record: keyLen(1) + key + sizeof(UserInfo)
bool loadUsersCache() {
  if (!SPIFFS.exists(USERS_CACHE_PATH)) return false;
  File f = SPIFFS.open(USERS_CACHE_PATH, "r");
  if (!f || f.size() < 2) { if (f) f.close(); return false; }

  uint16_t count = 0;
  f.read((uint8_t*)&count, 2);
  if (count > MAX_USERS) count = MAX_USERS;

  validKeys.clear();
  prefixIndex.clear();

  for (uint16_t i = 0; i < count; i++) {
    uint8_t keyLen = 0;
    if (f.read(&keyLen, 1) != 1 || keyLen == 0 || keyLen > 64) break;
    char keyBuf[65] = {0};
    if (f.read((uint8_t*)keyBuf, keyLen) != keyLen) break;

    UserInfo u = {};
    if (f.read((uint8_t*)&u, sizeof(UserInfo)) != sizeof(UserInfo)) break;

    String key(keyBuf);
    key.toLowerCase();
    validKeys[key] = u;
    if (key.length() >= 8) {
      prefixIndex[key.substring(0, 8)] = key;
    }
  }
  f.close();
  Serial.printf("[CACHE] Loaded %u users\n", (unsigned)validKeys.size());
  return !validKeys.empty();
}

bool saveUsersCache() {
  File f = SPIFFS.open(USERS_CACHE_PATH, "w");
  if (!f) return false;

  uint16_t count = min((uint16_t)validKeys.size(), (uint16_t)MAX_USERS);
  f.write((uint8_t*)&count, 2);

  uint16_t written = 0;
  for (auto& kv : validKeys) {
    if (written >= count) break;
    uint8_t keyLen = min((int)kv.first.length(), 64);
    f.write(&keyLen, 1);
    f.write((const uint8_t*)kv.first.c_str(), keyLen);
    f.write((const uint8_t*)&kv.second, sizeof(UserInfo));
    written++;
  }
  f.close();
  return true;
}

bool loadBleConfigCache() {
  if (!SPIFFS.exists(BLE_CONFIG_PATH)) return false;
  File f = SPIFFS.open(BLE_CONFIG_PATH, "r");
  if (!f) return false;
  String s = f.readString();
  f.close();

  // รูปแบบง่าย: uuid=....\ncompany=0x....
  int u = s.indexOf("uuid=");
  int c = s.indexOf("company=");
  if (u >= 0) {
    int end = s.indexOf('\n', u);
    targetUUID = s.substring(u + 5, end > 0 ? end : s.length());
    targetUUID.trim();
    targetUUID.toLowerCase();
  }
  if (c >= 0) {
    int end = s.indexOf('\n', c);
    String num = s.substring(c + 8, end > 0 ? end : s.length());
    num.trim();
    companyID = (uint16_t)strtol(num.c_str(), nullptr, 0);
  }
  return targetUUID.length() > 0;
}

bool saveBleConfigCache() {
  File f = SPIFFS.open(BLE_CONFIG_PATH, "w");
  if (!f) return false;
  f.printf("uuid=%s\ncompany=0x%04X\n", targetUUID.c_str(), companyID);
  f.close();
  return true;
}

bool enqueueAttendance(const PendingAtt& item) {
  std::vector<PendingAtt> q;
  loadPendingQueue(q);
  if (q.size() >= 50) return false;   // ป้องกันเต็ม
  q.push_back(item);
  return savePendingQueue(q);
}

bool loadPendingQueue(std::vector<PendingAtt>& out) {
  out.clear();
  if (!SPIFFS.exists(PENDING_QUEUE_PATH)) return true;
  File f = SPIFFS.open(PENDING_QUEUE_PATH, "r");
  if (!f) return false;
  while (f.available() >= (int)sizeof(PendingAtt)) {
    PendingAtt item = {};
    if (f.read((uint8_t*)&item, sizeof(PendingAtt)) == sizeof(PendingAtt)) {
      out.push_back(item);
    } else break;
  }
  f.close();
  return true;
}

bool savePendingQueue(const std::vector<PendingAtt>& q) {
  File f = SPIFFS.open(PENDING_QUEUE_PATH, "w");
  if (!f) return false;
  for (auto& item : q) {
    f.write((const uint8_t*)&item, sizeof(PendingAtt));
  }
  f.close();
  return true;
}
