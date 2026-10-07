// totp.cpp
#include "totp.h"
#include "config.h"
#include <mbedtls/md.h>
#include <time.h>

bool generateTotpHash(const char* secretKey, int offset, uint8_t out4[4]) {
  if (!secretKey || !secretKey[0]) return false;

  time_t now = time(nullptr);
  if (now < 1700000000L) return false;   // NTP ยังไม่พร้อม

  int64_t timeBlock = (static_cast<int64_t>(now) / TOTP_STEP_SECONDS) + offset;
  uint8_t timeBytes[8];
  for (int i = 7; i >= 0; --i) {
    timeBytes[i] = static_cast<uint8_t>(timeBlock & 0xFF);
    timeBlock >>= 8;
  }

  // lowercase + trim (ตรงกับ Python)
  char keyBuf[64];
  size_t len = 0;
  for (const char* p = secretKey; *p && len < sizeof(keyBuf) - 1; ++p) {
    if (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') continue;
    keyBuf[len++] = (*p >= 'A' && *p <= 'Z') ? (*p + 32) : *p;
  }
  keyBuf[len] = 0;
  if (len == 0) return false;

  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  if (mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1) != 0) {
    mbedtls_md_free(&ctx);
    return false;
  }

  uint8_t digest[32];
  mbedtls_md_hmac_starts(&ctx, (const unsigned char*)keyBuf, len);
  mbedtls_md_hmac_update(&ctx, timeBytes, 8);
  mbedtls_md_hmac_finish(&ctx, digest);
  mbedtls_md_free(&ctx);

  memcpy(out4, digest, 4);
  return true;
}
