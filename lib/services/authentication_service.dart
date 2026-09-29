import 'firestore_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../services/logdebug_service.dart';
import 'generatekey_service.dart';

class AuthenticationService {
  static const storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  /// เรียกเมื่อ OTP ถูกต้อง — สร้าง secret ใหม่ เก็บเครื่อง + Firestore
  static Future<bool> login(
    String studentId,
    String otp,
    int expiryTime,
  ) async {
    final FirestoreService firestoreService = FirestoreService();
    final doc = await firestoreService.getUser(studentId);
    final dataTimr = DateTime.now().millisecondsSinceEpoch;

    if (!doc.exists) {
      log("ไม่พบข้อมูลผู้ใช้");
      return false;
    }

    final data = doc.data() as Map<String, dynamic>?;
    final String dbOtp = data?['current_otp'] ?? '';

    if (dbOtp.isEmpty || otp != dbOtp || dataTimr > expiryTime) {
      log("otp input: $otp");
      log("otp db: $dbOtp");
      return false;
    }

    // --- Login สำเร็จ: หมุน secret ใหม่ (คีย์เดียว) ---
    final String newSecret = generateKey(32, "key");

    await storage.write(key: 'student_id', value: studentId);
    await storage.write(key: 'my_ble_secret', value: newSecret);
    // ลบคีย์เก่าแบบสองชั้น (ถ้ามี)
    await storage.delete(key: 'my_secret_key');
    await storage.delete(key: 'my_offline_secret');

    await FirestoreService().updateUser(studentId, {
      'otp_expiry': 0,
      'current_otp': '',
      'loginStatus': true,
      // ฟิลด์ key = secret ตัวเดียว ใช้ทำ TOTP ทั้งระบบ
      'key': newSecret,
      // ล้าง offline_secret เก่า (ไม่ใช้แล้ว) — ถ้าต้องการคงไว้เพื่อ migration ลบบรรทัดนี้ได้
      'offline_secret': '',
    });

    log("LOGIN studentId='$studentId' secret rotated (${newSecret.substring(0, 8)}...)");
    return true;
  }
}
