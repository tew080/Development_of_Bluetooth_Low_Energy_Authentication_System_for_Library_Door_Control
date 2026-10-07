// ble_scanner.cpp
#include "ble_scanner.h"
#include "config.h"
#include "shared_state.h"
#include "totp.h"
#include "firebase_manager.h"
#include "indicators.h"
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <esp_bt.h>

static BLEScan* pScan = nullptr;
static uint32_t unlockStartMs = 0;

// งานบันทึกเวลาที่ต้องทำนอกBLE callback (HTTPS บล็อกนาน -> ทำให้ BTC_TASK ติด watchdog)
static volatile bool pendingAtt = false;
static UserInfo pendingUser;
static String pendingKey;

static String bytesToHex(const uint8_t* d, size_t n) {
  String h; h.reserve(n * 2);
  for (size_t i = 0; i < n; i++) {
    char b[3]; sprintf(b, "%02x", d[i]); h += b;
  }
  return h;
}

class AdvCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) override {
    if (isProcessing || targetUUID.length() == 0) return;

    // 1. ตรวจสอบเบื้องต้นว่ามี Service UUID ตรงกับ Target UUID หรือไม่
    // แทนที่การเช็ค UUID เดิมใน ble_scanner.cpp
    bool hasUUID = false;
    if (dev.haveServiceUUID()) {
        String devUUID = dev.getServiceUUID().toString().c_str();
        devUUID.toLowerCase();
        devUUID.replace("-", "");
        devUUID.replace("0x", "");
        
        String cleanTarget = targetUUID;
        cleanTarget.toLowerCase();
        cleanTarget.replace("-", "");

        // เช็คว่า UUID สั้น (เช่น feaa) มีอยู่ใน targetUUID หรือไม่
        if (cleanTarget.indexOf(devUUID) >= 0 || devUUID.indexOf(cleanTarget) >= 0) {
            hasUUID = true;
        }
    }

    if (!hasUUID) return;

    // --- เมื่อเจอแพ็กเกจที่มี Target UUID ตรงกัน พิมพ์ Log ตรวจสอบทันที ---
    int rssi = dev.getRSSI();
    String macAddr = dev.getAddress().toString().c_str();
    Serial.printf("\n[BLE RECV] Detected target UUID from MAC: %s | RSSI: %d dBm (Threshold: %d dBm)\n",
                  macAddr.c_str(), rssi, RSSI_THRESHOLD);

    // 2. เช็คค่า RSSI
    if (rssi < RSSI_THRESHOLD) {
      Serial.printf(" └─ ❌ REJECT: RSSI too weak (%d < %d)\n", rssi, RSSI_THRESHOLD);
      return;
    }

    // 3. เช็ค Manufacturer Data
    if (!dev.haveManufacturerData()) {
      Serial.println(" └─ ❌ REJECT: No Manufacturer Data in package");
      return;
    }

    auto manStr = dev.getManufacturerData();      // ใช้ได้ทั้ง std::string และ String
    size_t manLen = manStr.length();
    if (manLen < 6) {
    Serial.printf(" └─ ❌ REJECT: Manufacturer Data length too short (%d bytes)\n", manLen);
    return;
    }

    const uint8_t* man = reinterpret_cast<const uint8_t*>(manStr.c_str());
    uint16_t recvCID = (uint16_t)man[0] | ((uint16_t)man[1] << 8);

    Serial.printf(" └─ Company ID: 0x%04X (Expected: 0x%04X)\n", recvCID, companyID);

    // 4. เช็ค Company ID
    if (recvCID != companyID) {
      Serial.println(" └─ ❌ REJECT: Company ID mismatch");
      return;
    }

    const uint8_t* payload = man + 2;
    size_t plen = manLen - 2;

    uint8_t flag = 0;
    const uint8_t* data4 = nullptr;
    const char* mode = "unknown";

    if (plen >= 5) {
      flag = payload[0];
      data4 = payload + 1;
    } else if (plen == 4) {
      data4 = payload;
      mode = "legacy";
    } else {
      Serial.printf(" └─ ❌ REJECT: Invalid payload size (%d bytes)\n", plen);
      return;
    }

    String manHex = bytesToHex(man, manLen);
    Serial.printf(" └─ FULL Manufacturer Data (%d bytes): %s\n", manLen, manHex.c_str());
    Serial.printf(" └─ First 2 bytes as CID: 0x%04X\n", recvCID);

    String payloadHex = bytesToHex(data4, 4);
    Serial.printf(" └─ Mode Flag: 0x%02X | Payload Data (4B Hex): %s\n", flag, payloadHex.c_str());

    String matchedKey;
    UserInfo matchedUser;
    bool found = false;

    // 5. เช็คความถูกต้องของ Key / TOTP Hash กับข้อมูลใน RAM
    if (flag == 0x01 || strcmp(mode, "legacy") == 0) {
      mode = (flag == 0x01) ? "online" : "legacy";

      // Exact match
      auto it = validKeys.find(payloadHex);
      if (it != validKeys.end()) {
        matchedKey = it->first;
        matchedUser = it->second;
        found = true;
      } else {
        // PrefixIndex match
        auto pit = prefixIndex.find(payloadHex);
        if (pit != prefixIndex.end()) {
          auto kit = validKeys.find(pit->second);
          if (kit != validKeys.end()) {
            matchedKey = kit->first;
            matchedUser = kit->second;
            found = true;
          }
        } else {
          // Fallback linear
          for (auto& kv : validKeys) {
            if (kv.first.startsWith(payloadHex)) {
              matchedKey = kv.first;
              matchedUser = kv.second;
              found = true;
              break;
            }
          }
        }
      }
    } else if (flag == 0x02) {
      mode = "offline";
      // TOTP Check
      for (auto& kv : validKeys) {
        const char* base = (kv.second.offline_secret[0]) ? kv.second.offline_secret : kv.first.c_str();
        for (int off = -1; off <= 1; off++) {
          uint8_t exp[4];
          if (!generateTotpHash(base, off, exp)) continue;
          if (memcmp(exp, data4, 4) == 0) {
            matchedKey = kv.first;
            matchedUser = kv.second;
            found = true;
            break;
          }
        }
        if (found) break;
      }
    }

    if (!found) {
      Serial.printf(" └─ ❌ REJECT: Payload/TOTP mismatch (%s) — Key count in RAM: %u\n",
                    payloadHex.c_str(), (unsigned)validKeys.size());
      return;
    }

    // 6. เช็ค Cooldown
    uint32_t now = millis();
    auto tit = lastScannedMs.find(matchedKey);
    if (tit != lastScannedMs.end() && (now - tit->second) < (COOLDOWN_SECONDS * 1000UL)) {
      uint32_t remainSec = COOLDOWN_SECONDS - ((now - tit->second) / 1000);
      Serial.printf(" └─ ⚠️ REJECT: Cooldown active for user %s (Wait %u sec)\n",
                    matchedUser.first_name, remainSec);
      return;
    }
    lastScannedMs[matchedKey] = now;

    // ยืนยันสำเร็จ
    isProcessing = true;
    unlockStartMs = now;
    setLedUnlock();
    buzzSuccess();

    Serial.printf(" └─ ✅ MATCH SUCCESS! Mode: %s | User: %s %s (DocID: %s)\n",
                  mode, matchedUser.first_name, matchedUser.last_name, matchedUser.doc_id);

    // ห้ามเรียก recordAttendance() ตรงนี้: callback รันใน BTC_TASK -> HTTPS นาน ๆ ทำให้ task_wdt reset
    pendingUser = matchedUser;
    pendingKey = matchedKey;
    pendingAtt = true;
  }
};

void bleInit() {
  esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);   // คืน RAM ของ Classic BT (~30KB) ก่อน init BLE
  BLEDevice::init("");
  pScan = BLEDevice::getScan();
  pScan->setAdvertisedDeviceCallbacks(new AdvCallbacks(), true);
  pScan->setActiveScan(true);
  pScan->setInterval(160);
  pScan->setWindow(120);
  Serial.println("[BLE] Scanner ready");
}

void bleScanOnce() {
  // ประมวลผลการบันทึกใน loop task (ไม่ใช่ใน BLE callback)
  if (pendingAtt) {
    UserInfo u = pendingUser;
    String k = pendingKey;
    pendingAtt = false;
    Serial.printf("[ATT] Recording for %s ...\n", u.doc_id);
    uint32_t t0 = millis();
    recordAttendance(u, k);
    Serial.printf("[ATT] Done in %lu ms | heap=%u\n", (unsigned long)(millis() - t0), ESP.getFreeHeap());
  }

  if (isProcessing) {
    if (millis() - unlockStartMs >= UNLOCK_DELAY_MS) {
      setLedIdle();
      isProcessing = false;
      unlockStartMs = 0;
    }
    return;
  }
  if (!pScan) return;
  pScan->start(SCAN_DURATION_MS / 1000.0f, false);
  pScan->clearResults();
}