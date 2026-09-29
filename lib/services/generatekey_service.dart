import 'dart:math';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'dart:convert';

/// สุ่มสตริงตามประเภท
/// - type "key" → hex (0-f)
/// - type "otp" → ตัวเลข 0-9
String generateKey(int len, String type) {
  final random = Random.secure();
  return List.generate(len, (index) {
    if (type == "key") {
      return random.nextInt(16).toRadixString(16);
    } else if (type == "otp") {
      return random.nextInt(10).toString();
    }
    return '0';
  }).join();
}

/// TOTP แบบคีย์เดียว
///
/// Secret หมุนเมื่อ login สำเร็จ
/// Payload บน BLE = HMAC-SHA256(secret, time_block)[0..3] เปลี่ยนทุก 30 วินาที
///
/// โครงสร้างที่ส่ง: [0x02][4 bytes hash] = 5 bytes (hex 10 ตัว)
/// ใช้ 0x02 เป็นเวอร์ชัน/ชนิดแพ็กเก็ต TOTP (ไม่ใช่แยก online/offline อีกต่อไป)
class TotpService {
  static const int timeStepSeconds = 30;

  /// สร้าง payload BLE (hex lowercase)
  static String generateBlePayload({
    required String secret,
    int timeStepSeconds = TotpService.timeStepSeconds,
  }) {
    final normalized = secret.trim().toLowerCase();
    final epochSeconds = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final timeBlock = epochSeconds ~/ timeStepSeconds;

    final timeBytes = Uint8List(8);
    ByteData.view(timeBytes.buffer).setInt64(0, timeBlock, Endian.big);

    final digest = Hmac(sha256, utf8.encode(normalized)).convert(timeBytes);
    final four = digest.bytes.sublist(0, 4);

    // [0x02] + 4-byte hash
    final full = <int>[0x02, ...four];
    return full.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}