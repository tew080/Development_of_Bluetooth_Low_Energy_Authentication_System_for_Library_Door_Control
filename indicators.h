// indicators.h
#pragma once
#include <Arduino.h>

void indicatorsInit();
void setLedIdle();
void setLedUnlock();
void buzzSuccess();           // สั้น ๆ 2 ครั้ง = สำเร็จ
void buzzError();             // ยาว 1 ครั้ง = error (optional)
