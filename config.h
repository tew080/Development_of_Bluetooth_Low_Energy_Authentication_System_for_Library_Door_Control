#pragma once

// ---------- ตรงกับ config.py เดิม ----------
#define RSSI_THRESHOLD          -66
#define UNLOCK_DELAY_MS         5000
#define COOLDOWN_SECONDS        20
#define TOTP_STEP_SECONDS       30

// ---------- Hardware ----------
#define LED_GREEN_PIN           18
#define LED_RED_PIN             19
#define BUZZER_PIN              4

// ---------- Limits & Optimization ----------
#define MAX_USERS               250     // จำกัด RAM (แต่ละ user ~120-150 bytes)
#define SCAN_DURATION_MS        1800    // สแกนสั้น ๆ ลด CPU/RF heat
#define SCAN_PAUSE_MS           400     // พักระหว่างรอบสแกน
#define USERS_REFRESH_MS        (10UL * 60 * 1000)  // รีเฟรช keys ทุก 10 นาที
#define QUEUE_FLUSH_MS          (30UL * 1000)
#define WIFI_RECONNECT_MS       10000
#define HEAP_WARN_BYTES         25000   // เตือนถ้า free heap ต่ำ

// ---------- Firestore ----------
#define COLLECTION_MEMBER       "member"
#define COLLECTION_ATTENDANCE   "attendance_logs"
#define COLLECTION_CONFIG       "connect"
#define FIELD_KEY               "key"

// ---------- SPIFFS paths ----------
#define USERS_CACHE_PATH        "/users.bin"
#define PENDING_QUEUE_PATH      "/pending.bin"
#define BLE_CONFIG_PATH         "/ble.cfg"
