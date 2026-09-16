// นำเข้าไลบรารี Firestore
import 'package:cloud_firestore/cloud_firestore.dart';

// คลาสสำหรับจัดการข้อมูลกับ Cloud Firestore
class FirestoreService {
  // สร้างตัวแปร _db เพื่ออ้างอิงถึง Instance ของ Firestore (เป็น private)
  final FirebaseFirestore _db = FirebaseFirestore.instance;

  // ฟังก์ชันดึงข้อมูล User แบบครั้งเดียว (One-time get)
  // ใช้สำหรับตอน Login เพื่อเช็ค Password
  Future<DocumentSnapshot> getUser(String studentId) {
    // เข้าไปที่ Collection 'member' และเลือก Doc ตาม studentId แล้วสั่ง get()
    return _db.collection('member').doc(studentId).get();
  }

  // ฟังก์ชันดึงข้อมูล User แบบครั้งเดียว (One-time get)
  // ใช้สำหรับตอน Login เพื่อเช็ค Password
  Future<DocumentSnapshot> getAdpack() {
    // เข้าไปที่ Collection 'students' และเลือก Doc ตาม studentId แล้วสั่ง get()
    return _db.collection('connect').doc('advertisingPackage').get();
  }

  // ฟังก์ชันดึงข้อมูล User แบบครั้งเดียว (One-time get)
  // ใช้สำหรับตอน Login เพื่อเช็ค Password
  Future<DocumentSnapshot> getEmailAdmin() {
    // เข้าไปที่ Collection 'students' และเลือก Doc ตาม studentId แล้วสั่ง get()
    return _db.collection('admin').doc('emailAdmin').get();
  }

  // ฟังก์ชันดึงข้อมูล User แบบ Real-time (Stream)
  // ใช้สำหรับหน้า Advertise เพื่อรอรับ Key ใหม่ทันทีเมื่อมีการเปลี่ยนแปลง
  Stream<DocumentSnapshot> getUserStream(String studentId) {
    // เข้าไปที่ Collection 'students' -> Doc studentId แล้วสั่ง snapshots()
    // snapshots() จะส่งข้อมูลมาเรื่อยๆ เมื่อ DB มีการเปลี่ยนแปลง
    return _db.collection('member').doc(studentId).snapshots();
  }

  Future<void> updateUser(String studentId, Map<String, dynamic> data) async {
    // ใช้ update เพื่อแก้ไขเฉพาะฟิลด์ที่ส่งไป (ข้อมูลอื่นจะไม่หาย)
    await _db.collection('member').doc(studentId).update(data);
  }
}
