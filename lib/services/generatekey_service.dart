import 'dart:math';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'dart:convert';

String generateKey(int len, String type) {
  // สร้างตัวสุ่มแบบ Secure เตรียมไว้
  final random = Random.secure();
  // สร้าง List ตามจำนวน len -> สุ่มเลข 0-15 -> แปลงเป็นฐาน 16 -> ต่อข้อความ
  return List.generate(len, (index) {
    if (type == "key") {
      return random.nextInt(16).toRadixString(16);
    } else if (type == "otp") {
      return random.nextInt(10).toString();
    } else {
      return 0;
    }
  }).join();
}

class TotpService {
  /// สร้าง Payload สำหรับ BLE Advertising
  /// โครงสร้าง: [Flag 1 byte] + [Data 4 bytes] = 5 bytes → Hex 10 ตัวอักษร
  ///
  /// Flag:
  ///   0x01 = Online  → Data = 4 bytes ของ Static Key (จาก Firestore)
  ///   0x02 = Offline → Data = 4 bytes แรกของ HMAC-SHA256(SecretKey, TimeBlock)
  static String generateDynamicPayload({
    required String secretKey,
    required bool isOffline,
    int timeStepSeconds = 30,
  }) {
    // 1. กำหนด Flag Byte
    final int flag = isOffline ? 0x02 : 0x01;

    List<int> data4Bytes;

    if (isOffline) {
      // ----- Offline Mode: TOTP / HMAC -----
      // 2. คำนวณ Time Block ณ วินาทีปัจจุบัน
      final int epochSeconds =
          DateTime.now().millisecondsSinceEpoch ~/ 1000;
      final int timeBlock = epochSeconds ~/ timeStepSeconds;

      // 3. แปลง timeBlock เป็น 8-byte Big-Endian
      final timeBytes = Uint8List(8);
      final byteData = ByteData.view(timeBytes.buffer);
      byteData.setInt64(0, timeBlock, Endian.big);

      // 4. HMAC-SHA256(SecretKey, TimeBlock)
      final keyBytes = utf8.encode(secretKey);
      final hmac = Hmac(sha256, keyBytes);
      final digest = hmac.convert(timeBytes);

      // 5. ตัดเอา 4 Bytes แรก
      data4Bytes = digest.bytes.sublist(0, 4);
    } else {
      // ----- Online Mode: ใช้ Static Key โดยตรง -----
      // secretKey ถูกสร้างเป็น hex string ความยาว 8 ตัว = 4 bytes
      try {
        final cleaned = secretKey.trim().toLowerCase();
        if (cleaned.length >= 8) {
          data4Bytes = [
            int.parse(cleaned.substring(0, 2), radix: 16),
            int.parse(cleaned.substring(2, 4), radix: 16),
            int.parse(cleaned.substring(4, 6), radix: 16),
            int.parse(cleaned.substring(6, 8), radix: 16),
          ];
        } else {
          // fallback ถ้าความยาวไม่พอ → ใช้ HMAC แทนเพื่อไม่ให้พัง
          final keyBytes = utf8.encode(secretKey);
          final hmac = Hmac(sha256, keyBytes);
          final digest = hmac.convert(utf8.encode("online"));
          data4Bytes = digest.bytes.sublist(0, 4);
        }
      } catch (_) {
        final keyBytes = utf8.encode(secretKey);
        final hmac = Hmac(sha256, keyBytes);
        final digest = hmac.convert(utf8.encode("online"));
        data4Bytes = digest.bytes.sublist(0, 4);
      }
    }

    // 6. ผนวก Payload: [Flag] + [4 bytes]
    final List<int> fullPayload = [flag, ...data4Bytes];

    // แปลงเป็น Hex String (lowercase) ส่งไปยัง Native
    return fullPayload
        .map((b) => b.toRadixString(16).padLeft(2, '0'))
        .join();
  }
}