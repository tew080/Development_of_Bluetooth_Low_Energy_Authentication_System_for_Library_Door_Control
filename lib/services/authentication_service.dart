// นำเข้า FirestoreService เพื่อดึงข้อมูล User
import 'firestore_service.dart';
// นำเข้าไลบรารี Flutter Secure Storage เพื่อจัดเก็บข้อมูลในเครื่อง
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
// นำเข้า LogdebugService
import '../services/logdebug_service.dart';

class AuthenticationService {
  //เข้ารหัสข้อมูลที่บันทึกในเครื่อง encryptedSharedPreferences = true (เพื่อให้อ่านข้อมูลที่เข้ารหัสได้)
  static const storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  // ฟังก์ชันสำหรับเข้าสู่ระบบ (Login)
  // รับค่า studentId และ password เข้ามา
  // คืนค่าเป็น Future<bool> (จริง/เท็จ)
  static Future<bool> login(
    String studentId,
    String otp,
    int expiryTime,
  ) async {
    // สร้าง Instance ของ FirestoreService เพื่อใช้งาน
    final FirestoreService firestoreService = FirestoreService();
    // เรียกดึงข้อมูล User จาก Firestore ตาม studentId
    final doc = await firestoreService.getUser(studentId);
    int dataTimr = DateTime.now().millisecondsSinceEpoch;

    // ตรวจสอบว่ามีเอกสาร (Document) นี้อยู่ในฐานข้อมูลหรือไม่
    if (!doc.exists) {
      log("ไม่พบข้อมูลผู้ใช้");
      return false;
    }

    final data = doc.data() as Map<String, dynamic>?;
    String dbOtp = data?['current_otp'] ?? '';

    if (dbOtp.isEmpty || otp != dbOtp || dataTimr > expiryTime) {
      log("otp input: $otp");
      log("otp db: $dbOtp");
      // ถ้าไม่มีข้อมูล ให้คืนค่า false (Login ไม่สำเร็จ)
      return false;
    } else {
      // ถ้ามีข้อมูล ให้ บันทึกข้อมูลลง Storage และคืนค่า true (Login สำเร็จ)
      await storage.write(key: 'student_id', value: studentId);
      await FirestoreService().updateUser(studentId, {'otp_expiry': 0});
      await FirestoreService().updateUser(studentId, {'current_otp': ''});
      await FirestoreService().updateUser(studentId, {'loginStatus': true});
      log("LOGIN studentId='$studentId'");
      return true;
    }
  }
}
