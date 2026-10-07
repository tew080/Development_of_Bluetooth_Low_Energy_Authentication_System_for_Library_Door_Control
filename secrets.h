#pragma once

// ========== WiFi ==========
#define WIFI_SSID           ""
#define WIFI_PASSWORD       ""

// ========== Firebase Service Account ==========
#define API_KEY             ""
#define FIREBASE_PROJECT_ID ""
#define FIREBASE_CLIENT_EMAIL "firebase-adminsdk-xxxxxx.iam.gserviceaccount.com"

// ต้องเป็น newline จริง (\n ใน C string) ห้ามมีตัวอักษร \ และ n จาก JSON ค้างอยู่
const char PRIVATE_KEY[] PROGMEM =
"-----BEGIN PRIVATE KEY-----\n"
"-----END PRIVATE KEY-----\n";