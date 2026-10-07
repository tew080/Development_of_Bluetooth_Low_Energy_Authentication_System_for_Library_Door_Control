// firebase_manager.cpp
#include "firebase_manager.h"
#include "config.h"
#include "cache_manager.h"
#include "secrets.h"
#include <WiFi.h>
#include <Firebase_ESP_Client.h>
#include <addons/TokenHelper.h>

FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig fbConfig;

static void copyStr(char* dst, size_t dstSize, const String& src) {
  strncpy(dst, src.c_str(), dstSize - 1);
  dst[dstSize - 1] = 0;
}

void wifiEnsureConnected() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("[WiFi] Connecting");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);          // ประหยัดไฟ + ลด heat
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(400);
    Serial.print(".");
    tries++;
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " OK" : " FAIL");
}

bool firebaseInit() {
  wifiEnsureConnected();

  // NTP (จำเป็นสำหรับ JWT ของ Service Account + TOTP)
  configTime(7 * 3600, 0, "pool.ntp.org", "time.google.com");
  Serial.print("[NTP] Waiting");
  time_t now = time(nullptr);
  int n = 0;
  while (now < 1700000000L && n < 40) {
    delay(400);
    Serial.print(".");
    now = time(nullptr);
    n++;
  }
  Serial.println(now > 1700000000L ? " OK" : " FAIL");

  fbConfig.api_key = API_KEY;
  fbConfig.service_account.data.client_email = FIREBASE_CLIENT_EMAIL;
  fbConfig.service_account.data.project_id   = FIREBASE_PROJECT_ID;
  fbConfig.service_account.data.private_key  = PRIVATE_KEY;

  fbConfig.token_status_callback = tokenStatusCallback;
  fbConfig.signer.preRefreshSeconds = 5 * 60;

  Firebase.begin(&fbConfig, &auth);
  Firebase.reconnectNetwork(true);
  fbdo.setResponseSize(12288);
  fbdo.setBSSLBufferSize(4096, 1024);

  Serial.print("[FB] Auth");
  uint32_t t0 = millis();
  while (!Firebase.ready() && millis() - t0 < 20000) {
    delay(300);
    Serial.print(".");
  }
  bool ok = Firebase.ready();
  Serial.println(ok ? " OK (Service Account)" : " FAIL → offline");
  isOffline = !ok;
  return ok;
}

bool firebaseReady() {
  return Firebase.ready() && (WiFi.status() == WL_CONNECTED);
}

void fetchBleConfig() {
  if (!firebaseReady()) {
    loadBleConfigCache();
    return;
  }

  String path = String(COLLECTION_CONFIG) + "/advertisingPackage";
  if (Firebase.Firestore.getDocument(&fbdo, FIREBASE_PROJECT_ID, "", path.c_str())) {
    FirebaseJson json;
    json.setJsonData(fbdo.payload().c_str());
    FirebaseJsonData r;

    if (json.get(r, "fields/uuid/stringValue")) {
      targetUUID = r.to<String>();
      targetUUID.toLowerCase();
      targetUUID.trim();
    }
    // companyID อาจเป็น integer หรือ string hex
    if (json.get(r, "fields/companyID/integerValue")) {
      companyID = (uint16_t)r.to<int>();
    } else if (json.get(r, "fields/companyID/stringValue")) {
      companyID = (uint16_t)strtol(r.to<String>().c_str(), nullptr, 0);
    }

    saveBleConfigCache();
    Serial.printf("[FB] BLE UUID=%s Company=0x%04X\n", targetUUID.c_str(), companyID);
    isOffline = false;
  } else {
    Serial.printf("[FB] fetchBleConfig: %s\n", fbdo.errorReason().c_str());
    loadBleConfigCache();
    isOffline = true;
  }
}

// ฟังก์ชันดึงข้อมูลผู้ใช้เฉพาะคนจาก Firestore Direct
bool fetchSingleUserFromFirestore(const String& docId, UserInfo& outUser) {
  if (!firebaseReady()) return false;

  String path = String(COLLECTION_MEMBER) + "/" + docId;
  if (Firebase.Firestore.getDocument(&fbdo, FIREBASE_PROJECT_ID, "", path.c_str())) {
    FirebaseJson json;
    json.setJsonData(fbdo.payload().c_str());
    FirebaseJsonData result;

    auto getStr = [&](const char* field, char* out, size_t outSz) {
      out[0] = 0;
      String p = String("fields/") + field + "/stringValue";
      if (json.get(result, p.c_str()) && result.success) {
        String v = result.to<String>();
        if (v.length() >= outSz) v = v.substring(0, outSz - 1);
        copyStr(out, outSz, v);
      }
    };

    getStr("last_status", outUser.last_status, sizeof(outUser.last_status));
    getStr("last_update_date", outUser.last_update_date, sizeof(outUser.last_update_date));
    return true;
  }
  return false;
}

// อ่านฟิลด์ string (หรือ integer) จาก document ใต้ base path เช่น "document/fields/"
static bool readField(FirebaseJson& j, const char* base, const char* field, char* out, size_t sz) {
  out[0] = 0;
  FirebaseJsonData r;
  String p = String(base) + field + "/stringValue";
  if (j.get(r, p.c_str()) && r.success) { copyStr(out, sz, r.to<String>()); return true; }
  p = String(base) + field + "/integerValue";
  if (j.get(r, p.c_str()) && r.success) { copyStr(out, sz, r.to<String>()); return true; }
  return false;
}

// โหลดเฉพาะสมาชิกที่มี key (key > "") ด้วย runQuery -> อ่านเท่าที่มีคีย์จริง ไม่ใช่ทั้ง 3,000 คน
bool loadUsersFromFirestore() {
  if (!firebaseReady()) {
    Serial.println("[FB] loadUsers: not ready → cache");
    return loadUsersCache();
  }

  Serial.printf("[FB] loadUsers(runQuery) heap before=%u\n", ESP.getFreeHeap());

  const int pageSize = 20;
  const char* fields[] = {"key", "offline_secret", "member_id", "student_id", "first_name",
                          "last_name", "faculty", "branch", "last_status", "last_update_date"};
  const int nFields = sizeof(fields) / sizeof(fields[0]);

  std::map<String, UserInfo> newKeys;
  std::map<String, String> newPrefix;
  int offset = 0, pages = 0, docsSeen = 0;
  bool apiError = false;

  while (newKeys.size() < (size_t)MAX_USERS && pages < 20) {
    pages++;

    FirebaseJson q;
    q.set("from/collectionId", COLLECTION_MEMBER);
    q.set("where/fieldFilter/field/fieldPath", FIELD_KEY);
    q.set("where/fieldFilter/op", "GREATER_THAN");
    q.set("where/fieldFilter/value/stringValue", "");
    q.set("limit", pageSize);
    q.set("offset", offset);
    for (int i = 0; i < nFields; i++) {
      q.set(String("select/fields/[") + i + "]/fieldPath", fields[i]);
    }

    // ตรวจ signature ของ runQuery ให้ตรงกับเวอร์ชันไลบรารีที่ติดตั้ง (ดูตัวอย่าง RunQuery)
    if (!Firebase.Firestore.runQuery(&fbdo, FIREBASE_PROJECT_ID, "", "", &q)) {
      Serial.printf("[FB] runQuery fail: %s\n", fbdo.errorReason().c_str());
      apiError = true;
      break;
    }

    Serial.printf("[FB] query page %d len=%d heap=%u\n", pages, (int)fbdo.payloadLength(), ESP.getFreeHeap());

    FirebaseJsonArray arr;
    if (!arr.setJsonArrayData(fbdo.payload().c_str())) {
      Serial.println("[FB] parse result array FAILED");
      apiError = true;
      fbdo.clear();
      break;
    }

    int docsThisPage = 0;
    for (size_t i = 0; i < arr.size(); i++) {
      FirebaseJsonData r;
      FirebaseJson el;
      if (!arr.get(r, i)) continue;
      r.get(el);

      FirebaseJsonData nameR;
      if (!el.get(nameR, "document/name") || !nameR.success) continue;  // element ว่าง (ไม่มีผลลัพธ์)
      String namePath = nameR.to<String>();
      String docId = namePath.substring(namePath.lastIndexOf('/') + 1);
      if (docId.length() == 0 || docId.length() >= 28) continue;
      docsThisPage++;
      docsSeen++;

      const char* B = "document/fields/";
      UserInfo u;
      memset(&u, 0, sizeof(u));
      copyStr(u.doc_id, sizeof(u.doc_id), docId);

      char keyBuf[128] = {0};
      readField(el, B, "key", keyBuf, sizeof(keyBuf));

      readField(el, B, "member_id", u.member_id, sizeof(u.member_id));
      if (!u.member_id[0]) readField(el, B, "student_id", u.member_id, sizeof(u.member_id));
      if (!u.member_id[0]) copyStr(u.member_id, sizeof(u.member_id), docId);

      readField(el, B, "offline_secret", u.offline_secret, sizeof(u.offline_secret));
      readField(el, B, "first_name", u.first_name, sizeof(u.first_name));
      readField(el, B, "last_name", u.last_name, sizeof(u.last_name));
      readField(el, B, "faculty", u.faculty, sizeof(u.faculty));
      readField(el, B, "branch", u.branch, sizeof(u.branch));
      readField(el, B, "last_status", u.last_status, sizeof(u.last_status));
      readField(el, B, "last_update_date", u.last_update_date, sizeof(u.last_update_date));

      if (!u.last_status[0]) strcpy(u.last_status, "Clock-OUT");

      String key(keyBuf);
      key.trim();
      key.toLowerCase();
      if (key.length() == 0) continue;
      if (!u.offline_secret[0]) copyStr(u.offline_secret, sizeof(u.offline_secret), key);

      newKeys[key] = u;
      if (key.length() >= 8) newPrefix[key.substring(0, 8)] = key;
      if (newKeys.size() >= (size_t)MAX_USERS) break;
    }

    arr.clear();
    fbdo.clear();

    if (docsThisPage < pageSize) break;   // หน้าสุดท้าย
    offset += pageSize;
    delay(30);
  }

  if (!apiError && !newKeys.empty()) {
    validKeys.swap(newKeys);
    prefixIndex.swap(newPrefix);
    saveUsersCache();                      // เซฟเฉพาะเมื่อมีข้อมูลจริง
    isOffline = false;
    Serial.printf("[FB] SUCCESS: %d docs, %u keys, pages=%d heap=%u\n",
                  docsSeen, (unsigned)validKeys.size(), pages, ESP.getFreeHeap());
    for (auto& kv : validKeys) {           // debug: แสดง prefix ที่โหลดได้
      Serial.printf("  key[%s...] → %s %s\n", kv.first.substring(0, 8).c_str(),
                    kv.second.first_name, kv.second.doc_id);
    }
    return true;
  }

  Serial.printf("[FB] load failed or 0 keys (apiError=%d, found=%u) → keep RAM/cache\n",
                apiError, (unsigned)newKeys.size());
  isOffline = true;
  if (validKeys.empty()) return loadUsersCache();   // ไม่ล้างของเดิมทิ้ง
  return false;
}

void recordAttendance(const UserInfo& user, const String& matchedKey) {
  time_t now = time(nullptr);
  struct tm ti;
  localtime_r(&now, &ti);
  char dateBuf[12], timeBuf[10];
  strftime(dateBuf, sizeof(dateBuf), "%Y-%m-%d", &ti);
  strftime(timeBuf, sizeof(timeBuf), "%H:%M:%S", &ti);

  UserInfo currentUser = user;

  // ดึงสถานะล่าสุดจาก Firebase ทุกครั้งก่อนคำนวณการบันทึก
  if (firebaseReady()) {
    UserInfo liveUser;
    if (fetchSingleUserFromFirestore(user.doc_id, liveUser)) {
      if (liveUser.last_status[0] != 0) {
        copyStr(currentUser.last_status, sizeof(currentUser.last_status), liveUser.last_status);
      }
      if (liveUser.last_update_date[0] != 0) {
        copyStr(currentUser.last_update_date, sizeof(currentUser.last_update_date), liveUser.last_update_date);
      }
    }
  }

  String newStatus;
  bool isFirst = false;
  if (strcmp(currentUser.last_update_date, dateBuf) != 0) {
    newStatus = "Clock-IN";
    isFirst = true;
  } else {
    newStatus = (strcmp(currentUser.last_status, "Clock-OUT") == 0) ? "Clock-IN" : "Clock-OUT";
  }

  // อัปเดต memory ทันที
  auto it = validKeys.find(matchedKey);
  if (it != validKeys.end()) {
    copyStr(it->second.last_status, sizeof(it->second.last_status), newStatus);
    copyStr(it->second.last_update_date, sizeof(it->second.last_update_date), dateBuf);
  }

  char logDocId[64];
  snprintf(logDocId, sizeof(logDocId), "%s_%c%c%c%c%c%c_%s",
           dateBuf,
           timeBuf[0], timeBuf[1], timeBuf[3], timeBuf[4], timeBuf[6], timeBuf[7],
           currentUser.doc_id);

  bool written = false;
  if (firebaseReady()) {
    FirebaseJson content;
    content.set("fields/last_status/stringValue", newStatus);
    content.set("fields/last_update_date/stringValue", dateBuf);
    content.set("fields/last_update_time/stringValue", timeBuf);
    content.set("fields/checkinoutStatus/booleanValue", true);

    String memberPath = String(COLLECTION_MEMBER) + "/" + currentUser.doc_id;
    if (Firebase.Firestore.patchDocument(&fbdo, FIREBASE_PROJECT_ID, "", memberPath.c_str(),
                                         content.raw(),
                                         "last_status,last_update_date,last_update_time,checkinoutStatus")) {
      FirebaseJson logContent;
      logContent.set("fields/member_id/stringValue", currentUser.doc_id);
      logContent.set("fields/first_name/stringValue", currentUser.first_name);
      logContent.set("fields/last_name/stringValue", currentUser.last_name);
      logContent.set("fields/faculty/stringValue", currentUser.faculty);
      logContent.set("fields/branch/stringValue", currentUser.branch);
      logContent.set("fields/date/stringValue", dateBuf);
      logContent.set("fields/time/stringValue", timeBuf);
      logContent.set("fields/action/stringValue", newStatus);
      logContent.set("fields/is_first_visit/booleanValue", isFirst);

      String logPath = String(COLLECTION_ATTENDANCE) + "/" + logDocId;
      if (Firebase.Firestore.createDocument(&fbdo, FIREBASE_PROJECT_ID, "", logPath.c_str(), logContent.raw())) {
        written = true;
        Serial.printf("[FB] %s → %s\n", currentUser.doc_id, newStatus.c_str());
      }
    }
  }

  if (!written) {
    PendingAtt item = {};
    copyStr(item.doc_id, sizeof(item.doc_id), currentUser.doc_id);
    copyStr(item.new_status, sizeof(item.new_status), newStatus);
    copyStr(item.date, sizeof(item.date), dateBuf);
    copyStr(item.time, sizeof(item.time), timeBuf);
    copyStr(item.first_name, sizeof(item.first_name), currentUser.first_name);
    copyStr(item.last_name, sizeof(item.last_name), currentUser.last_name);
    copyStr(item.log_doc_id, sizeof(item.log_doc_id), logDocId);
    item.is_first = isFirst ? 1 : 0;
    enqueueAttendance(item);
    Serial.println("[OFFLINE] Queued attendance");
    isOffline = true;
  }
}

void flushPendingQueue() {
  if (!firebaseReady()) return;
  std::vector<PendingAtt> q;
  if (!loadPendingQueue(q) || q.empty()) return;

  std::vector<PendingAtt> remain;
  int success = 0;
  for (auto& item : q) {
    FirebaseJson content;
    content.set("fields/last_status/stringValue", item.new_status);
    content.set("fields/last_update_date/stringValue", item.date);
    content.set("fields/last_update_time/stringValue", item.time);
    content.set("fields/checkinoutStatus/booleanValue", true);

    String memberPath = String(COLLECTION_MEMBER) + "/" + item.doc_id;
    bool ok = Firebase.Firestore.patchDocument(&fbdo, FIREBASE_PROJECT_ID, "", memberPath.c_str(),
                                               content.raw(),
                                               "last_status,last_update_date,last_update_time,checkinoutStatus");
    if (ok) {
      FirebaseJson logContent;
      logContent.set("fields/member_id/stringValue", item.doc_id);
      logContent.set("fields/first_name/stringValue", item.first_name);
      logContent.set("fields/last_name/stringValue", item.last_name);
      logContent.set("fields/date/stringValue", item.date);
      logContent.set("fields/time/stringValue", item.time);
      logContent.set("fields/action/stringValue", item.new_status);
      logContent.set("fields/is_first_visit/booleanValue", item.is_first != 0);

      String logPath = String(COLLECTION_ATTENDANCE) + "/" + item.log_doc_id;
      ok = Firebase.Firestore.createDocument(&fbdo, FIREBASE_PROJECT_ID, "", logPath.c_str(), logContent.raw());
    }
    if (ok) success++;
    else remain.push_back(item);
  }
  savePendingQueue(remain);
  if (success) Serial.printf("[QUEUE] Flushed %d, remain %u\n", success, (unsigned)remain.size());
}