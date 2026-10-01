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

/// บริการสร้าง payload BLE
///
/// โครงสร้าง Payload: [Flag 1 byte] + [Data 4 bytes] = 5 bytes (hex 10 ตัว)
/// - Flag 0x01 = Online  → Data = Static Key (4 bytes แรกของ secret)
/// - Flag 0x02 = Offline → Data = TOTP HMAC-SHA256(secret, time_block)[0..3]
///
/// แอปเลือก Online ก่อนเมื่อมีอินเทอร์เน็ต และใช้ Offline เมื่อไม่มีเน็ต
class TotpService {
  static const int timeStepSeconds = 30;

  /// สร้าง payload แบบ Online (static key)
  /// [0x01] + 4 bytes แรกของ secret (hex ของ secret ตัวแรก 8 ตัว)
  static String generateOnlinePayload({required String secret}) {
    final normalized = secret.trim().toLowerCase();
    // ใช้ 8 ตัวแรกของ hex secret = 4 bytes
    final staticHex = normalized.length >= 8
        ? normalized.substring(0, 8)
        : normalized.padRight(8, '0');

    final fourBytes = <int>[];
    for (var i = 0; i < 8; i += 2) {
      fourBytes.add(int.parse(staticHex.substring(i, i + 2), radix: 16));
    }

    final full = <int>[0x01, ...fourBytes];
    return full.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }

  /// สร้าง payload แบบ Offline (TOTP dynamic)
  /// [0x02] + HMAC-SHA256(secret, time_block)[0..3]
  static String generateOfflinePayload({
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

    final full = <int>[0x02, ...four];
    return full.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }

  /// สร้าง payload ตามสถานะเน็ต
  /// - hasInternet == true  → Online (static)
  /// - hasInternet == false → Offline (TOTP)
  static String generateBlePayload({
    required String secret,
    required bool hasInternet,
    int timeStepSeconds = TotpService.timeStepSeconds,
  }) {
    if (hasInternet) {
      return generateOnlinePayload(secret: secret);
    }
    return generateOfflinePayload(
      secret: secret,
      timeStepSeconds: timeStepSeconds,
    );
  }
}