#include "shared_state.h"

std::map<String, UserInfo> validKeys;
std::map<String, String> prefixIndex;
std::map<String, uint32_t> lastScannedMs;

String targetUUID = "";
uint16_t companyID = 0;

volatile bool isProcessing = false;
bool isOffline = false;
uint32_t lastUsersRefresh = 0;
uint32_t lastQueueFlush = 0;
uint32_t lastWifiCheck = 0;
