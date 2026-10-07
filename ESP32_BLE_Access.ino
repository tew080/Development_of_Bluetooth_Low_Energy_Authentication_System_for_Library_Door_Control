/**
 * ESP32-WROOM-32 BLE Access Control (Modular + Optimized)
 * - Service Account auth
 * - SPIFFS binary cache
 * - Online / Offline TOTP / Legacy
 * - LED + Buzzer
 * - Offline attendance queue
 */

#include <Arduino.h>
#include "config.h"
#include "shared_state.h"
#include "cache_manager.h"
#include "firebase_manager.h"
#include "ble_scanner.h"
#include "indicators.h"

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== ESP32 BLE Access (Modular) ===");

  indicatorsInit();
  buzzSuccess();
  if (!cacheInit()) {
    Serial.println("FATAL: SPIFFS failed");
    while (1) delay(1000);
  }

  // โหลด cache ชั่วคราวก่อนเพื่อเตรียมพร้อมเป็น fallback กรณีไม่มีเน็ต
  loadUsersCache();
  loadBleConfigCache();
  Serial.printf("[BOOT] Initial cache loaded: users=%u heap=%u\n",
                (unsigned)validKeys.size(), ESP.getFreeHeap());

  firebaseInit();

  // 1. ดึง BLE Config จาก Firebase ทุกครั้ง
  fetchBleConfig();

  // 2. ดึงข้อมูล Users จาก Firestore ทุกครั้งเสมอเมื่อเริ่มต้นระบบ
  Serial.println("[BOOT] Fetching users from Firestore...");
  if (!loadUsersFromFirestore()) {
    Serial.println("[BOOT] Firebase offline/failed — using SPIFFS cache fallback.");
  }

  lastUsersRefresh = millis();

  bleInit();
  setLedIdle();
  Serial.printf("Users in RAM: %u | Free heap: %u\n",
                (unsigned)validKeys.size(), ESP.getFreeHeap());
}

void loop() {
  // 1) BLE scan + unlock timeout
  bleScanOnce();
  delay(SCAN_PAUSE_MS);

  // 2) WiFi keep-alive
  if (millis() - lastWifiCheck > WIFI_RECONNECT_MS) {
    wifiEnsureConnected();
    lastWifiCheck = millis();
  }

  // 3) รีเฟรช keys จาก Firebase ตามรอบเวลา
  if (millis() - lastUsersRefresh > USERS_REFRESH_MS) {
    if (ESP.getFreeHeap() > HEAP_WARN_BYTES) {
      loadUsersFromFirestore();
    }
    lastUsersRefresh = millis();
  }

  // 4) flush offline queue
  if (millis() - lastQueueFlush > QUEUE_FLUSH_MS) {
    flushPendingQueue();
    lastQueueFlush = millis();
  }

  // 5) heap monitor
  static uint32_t lastHeapLog = 0;
  if (millis() - lastHeapLog > 60000) {
    Serial.printf("[SYS] heap=%u offline=%d users=%u\n",
                  ESP.getFreeHeap(), isOffline, (unsigned)validKeys.size());
    lastHeapLog = millis();
  }
}