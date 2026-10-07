#pragma once
#include <Arduino.h>
#include <map>
#include <vector>

struct UserInfo {
  char doc_id[28];
  char member_id[28];
  char first_name[32];
  char last_name[32];
  char faculty[24];
  char branch[24];
  char offline_secret[48];   // ถ้าว่าง ใช้ key แทน
  char last_status[12];      // "Clock-IN" / "Clock-OUT"
  char last_update_date[12]; // YYYY-MM-DD
};

struct PendingAtt {
  char doc_id[28];
  char new_status[12];
  char date[12];
  char time[10];
  char first_name[32];
  char last_name[32];
  char log_doc_id[64];
  uint8_t is_first;
};

// key (lowercase full secret) → UserInfo
extern std::map<String, UserInfo> validKeys;
// prefix 8 hex → key (เร่ง Online match)
extern std::map<String, String> prefixIndex;
// cooldown
extern std::map<String, uint32_t> lastScannedMs;

extern String targetUUID;
extern uint16_t companyID;

extern volatile bool isProcessing;
extern bool isOffline;
extern uint32_t lastUsersRefresh;
extern uint32_t lastQueueFlush;
extern uint32_t lastWifiCheck;
