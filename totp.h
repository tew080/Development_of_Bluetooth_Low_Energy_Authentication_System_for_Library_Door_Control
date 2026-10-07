// totp.h
#pragma once
#include <Arduino.h>

bool generateTotpHash(const char* secretKey, int offset, uint8_t out4[4]);
