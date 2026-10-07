// cache_manager.h
#pragma once
#include <Arduino.h>
#include "shared_state.h"

bool cacheInit();
bool loadUsersCache();
bool saveUsersCache();
bool loadBleConfigCache();
bool saveBleConfigCache();
bool enqueueAttendance(const PendingAtt& item);
bool loadPendingQueue(std::vector<PendingAtt>& out);
bool savePendingQueue(const std::vector<PendingAtt>& q);
