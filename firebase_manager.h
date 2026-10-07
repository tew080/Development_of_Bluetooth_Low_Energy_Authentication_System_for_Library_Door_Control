// firebase_manager.h
#pragma once
#include <Arduino.h>
#include "shared_state.h"

bool firebaseInit();
bool firebaseReady();
void fetchBleConfig();
bool loadUsersFromFirestore();
bool fetchSingleUserFromFirestore(const String& docId, UserInfo& outUser);
void recordAttendance(const UserInfo& user, const String& matchedKey);
void flushPendingQueue();
void wifiEnsureConnected();