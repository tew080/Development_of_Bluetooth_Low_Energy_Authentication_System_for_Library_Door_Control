// indicators.cpp
#include "indicators.h"
#include "config.h"

// ฟังก์ชันสำหรับสร้างความถี่เสียง (Hz) ให้ Passive Buzzer
static void playTone(int pin, int frequencyHz, int durationMs) {
  if (frequencyHz <= 0) return;
  long delayMicro = 1000000L / (frequencyHz * 2);
  long numCycles = (long)frequencyHz * durationMs / 1000L;

  for (long i = 0; i < numCycles; i++) {
    digitalWrite(pin, HIGH);
    delayMicroseconds(delayMicro);
    digitalWrite(pin, LOW);
    delayMicroseconds(delayMicro);
  }
}

void indicatorsInit() {
  pinMode(LED_GREEN_PIN, OUTPUT);
  pinMode(LED_RED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  setLedIdle();
}

void setLedIdle() {
  digitalWrite(LED_GREEN_PIN, LOW);
  digitalWrite(LED_RED_PIN, HIGH);
}

void setLedUnlock() {
  digitalWrite(LED_GREEN_PIN, HIGH);
  digitalWrite(LED_RED_PIN, LOW);
}

void buzzSuccess() {
  // เสียงเซเว่นฯ โทนนุ่มทุ้ม (E5 -> C5 -> D5 -> G4)
  playTone(BUZZER_PIN, 659, 160); delay(40);  // โน้ต E5 (มี)
  playTone(BUZZER_PIN, 523, 160); delay(40);  // โน้ต C5 (โด)
  playTone(BUZZER_PIN, 587, 160); delay(40);  // โน้ต D5 (เร)
  playTone(BUZZER_PIN, 392, 300);             // โน้ต G4 (ซอล)
}

void buzzError() {
  playTone(BUZZER_PIN, 440, 250);             // โน้ต A4 เสียงเตือนทุ้มไม่บาดหู
}