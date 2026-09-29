import 'package:flutter/material.dart';
// นำเข้า AuthenticationService
import '../services/authentication_service.dart';
// นำเข้า LogdebugService
import '../services/logdebug_service.dart';
// นำเข้าหน้า BleAdvertisePage
import 'bleadvertise_page.dart';
// นำเข้า Google Sign-In สำหรับการยืนยันตัวตนด้วย Google
import 'package:google_sign_in/google_sign_in.dart';
// นำเข้า Mailer สำหรับการส่งอีเมล
import 'package:mailer/mailer.dart';
import 'package:mailer/smtp_server.dart';
// นำเข้า GenerateKeyService สำหรับการสร้างคีย์
import '../services/generatekey_service.dart';
// นำเข้า FirestoreService
import '../services/firestore_service.dart';

import 'dart:math';
import 'package:cloud_firestore/cloud_firestore.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() {
    return _LoginPageState();
  }
}

class _LoginPageState extends State<LoginPage> {
  // กำหนดค่าเริ่มต้นให้กับตัวแปร error เป็น ค่าว่าง
  String error = '';
  // กำหนดค่าเริ่มต้นให้กับตัวแปร loading เป็น false
  bool loading = false;
  // ตัวแปร Boolean เพื่อติดตามว่าการลงชื่อเข้าใช้ด้วย Google ได้รับการเริ่มต้นแล้วหรือไม่
  bool _isGoogleSignInInitialized = false;
  // ตัวแปร Boolean เพื่อติดตามว่ารหัส OTP ถูกส่งออกไปแล้วหรือไม่
  bool _isOtpSent = false;
  // ตัวแปร Boolean เพื่อควบคุมการแสดงผลของฟิลด์แก้ไขอีเมล
  bool editEmail = false;
  // ตัวแปร Boolean เพื่อควบคุมการแสดงผลของปุ่มเลือกอีเมล
  bool pickEmail = false;
  // ตัวแปร String สำหรับเก็บอีเมลเป้าหมายที่จะส่ง OTP ไป
  String _targetEmail = '';
  // สร้าง Instance ของ FirestoreService เพื่อใช้งาน
  final FirestoreService firestoreService = FirestoreService();
  // อินสแตนซ์ของ GoogleSignIn สำหรับจัดการกระบวนการลงชื่อเข้าใช้
  final GoogleSignIn _googleSignIn = GoogleSignIn.instance;
  // อีเมลของระบบที่ใช้ในการส่ง OTP
  String _systemEmail = '';
  // รหัสผ่านแอปพลิเคชันของระบบที่ใช้ในการยืนยันตัวตนเพื่อส่ง OTP
  String _systemAppPassword = '';
  // อีเมลที่ผู้ใช้เลือกจากรายการ
  String selectedEmail = '';
  // อีเมลที่ผู้ใช้เลือกใหม่ (ใช้ในกรณีที่ต้องการเปลี่ยนอีเมล)
  String newSelectedEmail = '';
  // เวลาหมดอายุของ OTP
  int expiryTime = 0;
  // ตัวแปรสำหรับตรวจสอบอีเมลที่ผู้ใช้ป้อน
  String emaillCheck = '';
  // ตัวแปรสำหรับตรวจสอบอีเมลที่ผู้ใช้เลือก
  String selectedEmailCheck = '';
  // เส้นทางอีเมลที่ต้องการตรวจสอบ (ในกรณีที่ต้องการใช้อีเมลของมหาวิทยาลัย)
  String targetEmail = '@student.sru.ac.th';

  // State สำหรับโหมดลงทะเบียนบุคคลภายนอก
  bool isRegisterGuest = false;
  String _generatedEpId = '';
  String _tempOtp = '';
  int _tempOtpExpiry = 0;

  String targetID = 'ep';
  // รับค่าจาก รหัสนักศึกษาจาก TextField
  final studentIdCtrl = TextEditingController();
  // รับค่าจาก รหัสนักศึกษาจาก TextField
  final otpCtrl = TextEditingController();

  // Controllers ใหม่สำหรับบุคคลภายนอก
  String? _selectedPrefix; // ตัวแปรเก็บค่าคำนำหน้าที่เลือก
  final List<String> _prefixOptions = ['นาย', 'นาง', 'นางสาว']; // ตัวเลือกคำนำหน้า
  final firstNameCtrl = TextEditingController();
  final lastNameCtrl = TextEditingController();
  final branchCtrl = TextEditingController();
  final emailCtrl = TextEditingController();

  Future<void> _pickEmailAndSendOtp() async {
    GoogleSignInAccount? account;
    final studentId = studentIdCtrl.text.replaceAll(RegExp(r'\s+'), '');

    if (studentId.isEmpty) {
      setState(() {
        error = '*กรุณากรอกรหัสสมาชิก*';
        loading = false;
        studentIdCtrl.clear();
      });
      return;
    }

    final userCheck = await firestoreService.getUser(studentId);

    if (!userCheck.exists) {
      setState(() {
        error = '*ไม่พบข้อมูลผู้ใช้*';
        studentIdCtrl.clear();
      });
      return;
    }

    final userData = userCheck.data() as Map<String, dynamic>?;
    emaillCheck = userData?['email'] ?? '';

    if (emaillCheck.isEmpty) {
      setState(() {
        error = '*ไม่พบอีเมล*';
        studentIdCtrl.clear();
      });
      return;
    }

    // ล้าง Session เดิม เพื่อบังคับเปิด Pop-up เลือกบัญชีใหม่เสมอ
    await _googleSignIn.signOut();

    // ตรวจสอบว่าการลงชื่อเข้าใช้ด้วย Google ได้รับการเริ่มต้นแล้วหรือไม่ ถ้ายัง ให้เริ่มต้น
    if (!_isGoogleSignInInitialized) {
      await _googleSignIn.initialize();
      _isGoogleSignInInitialized = true;
    }

    // ดำเนินการยืนยันตัวตนด้วย Google และขอสิทธิ์เข้าถึงอีเมล
    account = await _googleSignIn.authenticate(scopeHint: ['email']);
    // ดึงอีเมลที่ผู้ใช้เลือกจากข้อมูลบัญชี
    selectedEmail = account.email;

    // ตรวจสอบว่าโดเมนของอีเมลตรงกับที่ต้องการหรือไม่
    if (!studentId.trim().toLowerCase().startsWith(targetID)) {
      if (!selectedEmail.trim().toLowerCase().endsWith(targetEmail)) {
        setState(() {
          error = '*กรุณาใช้อีเมลของมหาวิทยาลัย (@student.sru.ac.th) เท่านั้น*';
          loading = false;
        });
        await _googleSignIn.signOut();
        return; // จบการทำงานทันทีถ้าโดเมนไม่ถูกต้อง
      }
    }

    selectedEmailCheck = userCheck['email'];
    // อัปเดตสถานะของ UI
    setState(() {
      // ให้เซ็ตตัวแปร loading = true เพื่อป้องกันการกดปุ่ม Login ซ้ำ
      loading = true;
    });
    if (selectedEmail != emaillCheck) {
      setState(() {
        error = 'เมลที่ใช้ลงทะเบียนไม่ตรง: $emaillCheck ';
        // ให้เซ็ตตัวแปร loading = true เพื่อป้องกันการกดปุ่ม Login ซ้ำ
        loading = false;
      });
      // ป้องกันการค้างของ Session
      await _googleSignIn.signOut();
      return;
    }

    // สร้างรหัส OTP สุ่ม 6 หลัก
    String otp = generateKey(6, "otp");

    expiryTime = DateTime.now()
        .add(const Duration(seconds: 60))
        .millisecondsSinceEpoch;

    // บันทึก OTP ลง Firestore
    await FirestoreService().updateUser(studentId, {
      'current_otp': otp,
      'otp_expiry': expiryTime,
    });

    log("ผู้ใช้เลือกอีเมล: $selectedEmail");

    // ดึงข้อมูล UUID,CompanyID ของ Advertising Package จากใน Firebase
    final doc = await firestoreService.getEmailAdmin();
    _systemEmail = doc['email'];
    _systemAppPassword = doc['emailAppPassword'];

    // กำหนดค่า SMTP server โดยใช้ข้อมูลอีเมลและรหัสผ่านของระบบ
    final smtpServer = gmail(_systemEmail, _systemAppPassword);
    // สร้างข้อความอีเมล
    final message = Message()
      // ตั้งค่าผู้ส่ง
      ..from = Address(_systemEmail, 'ระบบยืนยันตัวตนเข้าใช้แอพ BLE')
      // เพิ่มผู้รับอีเมล (อีเมลที่ผู้ใช้ป้อน)
      ..recipients.add(selectedEmail)
      // ตั้งค่าหัวข้ออีเมล
      ..subject = 'รหัส OTP ของคุณคือ: $otp'
      // ตั้งค่าเนื้อหาอีเมลเป็น HTML
      ..html =
          """
                <div style="font-family: sans-serif; padding: 20px;">
                  <h2>รหัสยืนยันตัวตน (OTP)</h2>
                  <p>รหัสสำหรับเข้าสู่ระบบของคุณคือ:</p>
                  <h1 style="color: #2196F3; font-size: 32px; letter-spacing: 5px;">$otp</h1>
                  <p style="color: #888;">นำรหัสนี้ไปกรอกในแอปพลิเคชัน BLE</p>
                  <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 60 วินาที*</p>
                </div>
              """;

    // ส่งอีเมลพร้อม OTP ไปยังผู้รับ
    await send(message, smtpServer);

    // อัปเดตสถานะของ UI
    setState(() {
      pickEmail = true;
      error = '';
      // ตั้งค่าว่า OTP ถูกส่งแล้ว
      _isOtpSent = true;
      // ตั้งค่าอีเมลเป้าหมาย
      _targetEmail = selectedEmail;
      // ปิดสถานะการโหลด
      loading = false;
    });
    // ป้องกันการค้างของ Session
    await _googleSignIn.signOut();
  }

  Future<void> _registerGuestAndSendOtp() async {
    final firstName = firstNameCtrl.text.trim();
    final lastName = lastNameCtrl.text.trim();
    final email = emailCtrl.text.trim();

    // 1. ตรวจสอบว่าเลือกคำนำหน้าหรือยัง
    if (_selectedPrefix == null || _selectedPrefix!.isEmpty) {
      setState(() => error = '*กรุณาเลือกคำนำหน้า*');
      return;
    }

    // 2. ตรวจสอบว่ากรอกฟิลด์บังคับครบหรือไม่
    if (firstName.isEmpty || lastName.isEmpty || email.isEmpty) {
      setState(() => error = '*กรุณากรอกข้อมูลที่บังคับให้ครบถ้วน*');
      return;
    }

    // 3. ตรวจสอบประเภทข้อมูล ชื่อ-นามสกุล (ต้องเป็นตัวอักษรเท่านั้น ห้ามมีตัวเลขหรืออักขระพิเศษ)
    if (!RegExp(r"^[a-zA-Zก-๙\s]+$").hasMatch(firstName) ||
        !RegExp(r"^[a-zA-Zก-๙\s]+$").hasMatch(lastName)) {
      setState(() => error = '*ชื่อและนามสกุลต้องเป็นตัวอักษรเท่านั้น*');
      return;
    }

    // 4. ตรวจสอบรูปแบบอีเมล
    if (!RegExp(
            r"^[a-zA-Z0-9.a-zA-Z0-9.!#$%&'*+-/=?^_`{|}~]+@[a-zA-Z0-9-]+\.[a-zA-Z]+")
        .hasMatch(email)) {
      setState(() => error = '*รูปแบบอีเมลไม่ถูกต้อง*');
      return;
    }

    setState(() {
      loading = true;
      error = '';
    });

    // สุ่มรหัสผู้ใช้งาน epXXXX
    final randomDigits = Random().nextInt(10000).toString().padLeft(4, '0');
    _generatedEpId = 'ep$randomDigits';

    // สร้าง OTP
    _tempOtp = generateKey(6, "otp");
    _tempOtpExpiry =
        DateTime.now().add(const Duration(seconds: 60)).millisecondsSinceEpoch;

    try {
      final doc = await firestoreService.getEmailAdmin();
      _systemEmail = doc['email'];
      _systemAppPassword = doc['emailAppPassword'];

      final smtpServer = gmail(_systemEmail, _systemAppPassword);
      final message = Message()
        ..from = Address(_systemEmail, 'ระบบลงทะเบียนเข้าใช้แอพ BLE')
        ..recipients.add(email)
        ..subject = 'รหัส OTP สำหรับลงทะเบียนของคุณคือ: $_tempOtp'
        ..html = """
             <div style="font-family: sans-serif; padding: 20px;">
               <h2>รหัสยืนยันการลงทะเบียน (OTP)</h2>
               <p>รหัสสำหรับการลงทะเบียนของคุณคือ:</p>
               <h1 style="color: #2196F3; font-size: 32px; letter-spacing: 5px;">$_tempOtp</h1>
               <p style="color: #888;">นำรหัสนี้ไปกรอกในแอปพลิเคชัน BLE</p>
               <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 60 วินาที*</p>
             </div>
           """;

      await send(message, smtpServer);

      setState(() {
        _isOtpSent = true;
        _targetEmail = email;
        loading = false;
      });
    } catch (e) {
      setState(() {
        error = 'เกิดข้อผิดพลาดในการส่งอีเมล: $e';
        loading = false;
      });
    }
  }

  Future<void> _editEmailAndSendOtp() async {
    GoogleSignInAccount? account;
    final studentId = studentIdCtrl.text.replaceAll(RegExp(r'\s+'), '');

    if (studentId.isEmpty) {
      setState(() {
        error = '*กรุณากรอกรหัสสมาชิก*';
        loading = false;
        studentIdCtrl.clear();
      });
      return;
    }

    final userCheck = await firestoreService.getUser(studentId);

    if (!userCheck.exists) {
      setState(() {
        error = '*ไม่พบข้อมูลผู้ใช้*';
        studentIdCtrl.clear();
      });
      return;
    }

    // ตรวจสอบว่าการลงชื่อเข้าใช้ด้วย Google ได้รับการเริ่มต้นแล้วหรือไม่ ถ้ายัง ให้เริ่มต้น
    if (!_isGoogleSignInInitialized) {
      await _googleSignIn.initialize();
      _isGoogleSignInInitialized = true;
    }

    // ดำเนินการยืนยันตัวตนด้วย Google และขอสิทธิ์เข้าถึงอีเมล
    account = await _googleSignIn.authenticate(scopeHint: ['email']);

    // ดึงอีเมลที่ผู้ใช้เลือกจากข้อมูลบัญชี
    newSelectedEmail = account.email;

    // ตรวจสอบว่ารหัสผู้ใช้งานขึ้นต้นด้วย 'ep' (บุคคลภายนอก) หรือไม่
    if (!studentId.trim().toLowerCase().startsWith(targetID)) {
      setState(() {
        error =
            '*กรุณาใช้รหัสผู้ใช้งานสำหรับบุคคลภายนอก (เช่น epXXXXX) เท่านั้น*';
        loading = false;
      });
      // หมายเหตุ: หากบุคคลภายนอกไม่ได้ล็อกอินผ่าน Google Sign-in สามารถลบบรรทัด signOut() ออกได้
      await _googleSignIn.signOut();
      return; // จบการทำงานทันที
    }

    selectedEmail = userCheck['email'];

    // อัปเดตสถานะของ UI
    setState(() {
      editEmail = true;
      // ให้เซ็ตตัวแปร loading = true เพื่อป้องกันการกดปุ่ม Login ซ้ำ
      loading = true;
    });
    // สร้างรหัส OTP สุ่ม 6 หลัก
    String otp = generateKey(6, "otp");

    expiryTime = DateTime.now()
        .add(const Duration(seconds: 60))
        .millisecondsSinceEpoch;

    // บันทึก OTP ลง Firestore
    await FirestoreService().updateUser(studentId, {
      'current_otp': otp,
      'otp_expiry': expiryTime,
    });

    // สั่ง SignOut เพื่อให้รอบหน้ากดเลือกบัญชีใหม่ได้
    await _googleSignIn.signOut();

    log("ผู้ใช้เลือกอีเมล: $selectedEmail");

    // ดึงข้อมูล UUID,CompanyID ของ Advertising Package จากใน Firebase
    final doc = await firestoreService.getEmailAdmin();
    _systemEmail = doc['email'];
    _systemAppPassword = doc['emailAppPassword'];

    // กำหนดค่า SMTP server โดยใช้ข้อมูลอีเมลและรหัสผ่านของระบบ
    final smtpServer = gmail(_systemEmail, _systemAppPassword);
    if (selectedEmail == '') {
      // สร้างข้อความอีเมล
      final message = Message()
        // ตั้งค่าผู้ส่ง
        ..from = Address(_systemEmail, 'ระบบยืนยันตัวตนเข้าใช้แอพ BLE')
        // เพิ่มผู้รับอีเมล (อีเมลที่ผู้ใช้ป้อน)
        ..recipients.add(newSelectedEmail)
        // ตั้งค่าหัวข้ออีเมล
        ..subject = 'รหัส OTP ของคุณคือ: $otp'
        // ตั้งค่าเนื้อหาอีเมลเป็น HTML
        ..html =
            """
                <div style="font-family: sans-serif; padding: 20px;">
                  <h2>รหัสยืนยันตัวตน (OTP)</h2>
                  <p>รหัสสำหรับเข้าสู่ระบบของคุณคือ:</p>
                  <h1 style="color: #2196F3; font-size: 32px; letter-spacing: 5px;">$otp</h1>
                  <p style="color: #888;">นำรหัสนี้ไปกรอกในแอปพลิเคชัน BLE</p>
                  <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 60 วินาที*</p>
                </div>
              """;

      // ส่งอีเมลพร้อม OTP ไปยังผู้รับ
      await send(message, smtpServer);
    } else {
      // สร้างข้อความอีเมล
      final message = Message()
        // ตั้งค่าผู้ส่ง
        ..from = Address(_systemEmail, 'ระบบยืนยันตัวตนเข้าใช้แอพ BLE')
        // เพิ่มผู้รับอีเมล (อีเมลที่ผู้ใช้ป้อน)
        ..recipients.add(selectedEmail)
        // ตั้งค่าหัวข้ออีเมล
        ..subject = 'รหัส OTP ของคุณคือ: $otp'
        // ตั้งค่าเนื้อหาอีเมลเป็น HTML
        ..html =
            """
                <div style="font-family: sans-serif; padding: 20px;">
                  <h2>รหัสยืนยันตัวตน (OTP)</h2>
                  <p>รหัสสำหรับเข้าสู่ระบบของคุณคือ:</p>
                  <h1 style="color: #2196F3; font-size: 32px; letter-spacing: 5px;">$otp</h1>
                  <p style="color: #888;">นำรหัสนี้ไปกรอกในแอปพลิเคชัน BLE</p>
                  <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 60 วินาที*</p>
                </div>
              """;

      // ส่งอีเมลพร้อม OTP ไปยังผู้รับ
      await send(message, smtpServer);
    }
    // อัปเดตสถานะของ UI
    setState(() {
      error = '';
      // ตั้งค่าว่า OTP ถูกส่งแล้ว
      _isOtpSent = true;
      // ตั้งค่าอีเมลเป้าหมายให้ตรงกับความเป็นจริง (ถ้าอีเมลเก่าว่างให้แสดงอีเมลใหม่, ถ้าไม่ว่างให้แสดงอีเมลเก่า)
      _targetEmail = selectedEmail.isEmpty ? newSelectedEmail : selectedEmail;
      // ปิดสถานะการโหลด
      loading = false;
    });

    // ป้องกันการค้างของ Session
    await _googleSignIn.signOut();
  }

  Future<void> _verifyOtp() async {
    final inputOtp = otpCtrl.text.replaceAll(RegExp(r'\s+'), '');
    if (inputOtp.isEmpty || inputOtp.length != 6) {
      setState(() {
        error = '*กรุณากรอกรหัส OTP 6 หลักให้ครบถ้วน*';
      });
      return;
    }

    setState(() {
      // เซ็ต loading = true ป้องกันการกดปุ่มซ้ำๆ
      loading = true;
    });

    // กรณีลงทะเบียนบุคคลภายนอก
    if (isRegisterGuest) {
      if (inputOtp == _tempOtp &&
          DateTime.now().millisecondsSinceEpoch < _tempOtpExpiry) {
        try {
          // บันทึกข้อมูลลง Firestore (บันทึก _selectedPrefix แทนตัวเดิม)
          await FirebaseFirestore.instance
              .collection('member')
              .doc(_generatedEpId)
              .set({
            'member_id': _generatedEpId,
            'prefix': _selectedPrefix,
            'first_name': firstNameCtrl.text.trim(),
            'last_name': lastNameCtrl.text.trim(),
            'faculty': 'บุคคลภายนอก',
            'branch': branchCtrl.text.trim(),
            'email': emailCtrl.text.trim(),
            'key': '',
            'loginStatus': true,
            'last_status': 'Clock-OUT',
            'last_update_date': '',
            'last_update_time': '',
            'current_otp': '',
            'otp_expiry': 0,
          });

          log("ลงทะเบียนและยืนยัน OTP สำเร็จ รหัส: $_generatedEpId");
          if (mounted) {
            setState(() {
              loading = false; // ปิดสถานะโหลดก่อนโชว์ Dialog
            });

            // แสดง Dialog แจ้งรหัสสมาชิกและให้แคปหน้าจอ
            showDialog(
              context: context,
              barrierDismissible: false, // บังคับให้ผู้ใช้ต้องกดปุ่มตกลง
              builder: (BuildContext dialogContext) {
                return AlertDialog(
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(20),
                  ),
                  title: const Column(
                    children: [
                      Icon(Icons.check_circle_rounded,
                          color: Color(0xFF2E7D32), size: 64),
                      SizedBox(height: 12),
                      Text(
                        'ลงทะเบียนสำเร็จ!',
                        style: TextStyle(
                          color: Color(0xFF2E7D32),
                          fontWeight: FontWeight.bold,
                          fontSize: 20,
                        ),
                      ),
                    ],
                  ),
                  content: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Text(
                        'รหัสสมาชิกสำหรับเข้าสู่ระบบของคุณคือ',
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 15, color: Colors.black87),
                      ),
                      const SizedBox(height: 16),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(
                            horizontal: 20, vertical: 14),
                        decoration: BoxDecoration(
                          color: const Color(0xFFE3F2FD),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: const Color(0xFF90CAF9)),
                        ),
                        child: SelectableText(
                          _generatedEpId,
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            fontSize: 28,
                            fontWeight: FontWeight.bold,
                            color: Color(0xFF1565C0),
                            letterSpacing: 3,
                          ),
                        ),
                      ),
                      const SizedBox(height: 20),
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: const Color(0xFFFFF3E0),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Text(
                          '⚠️ แนะนำให้แคปหน้าจอหน้านี้เอาไว้\nเพื่อใช้เป็นรหัสในการเข้าสู่ระบบครั้งต่อไป',
                          style: TextStyle(
                            color: Color(0xFFE65100),
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                            height: 1.4,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ],
                  ),
                  actions: [
                    Center(
                      child: ElevatedButton(
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF1565C0),
                          foregroundColor: Colors.white,
                          minimumSize: const Size(180, 48),
                          elevation: 0,
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(14),
                          ),
                        ),
                        child: const Text(
                          'ตกลง / เข้าสู่ระบบ',
                          style: TextStyle(
                              fontSize: 16, fontWeight: FontWeight.w600),
                        ),
                        onPressed: () {
                          Navigator.pop(dialogContext); // ปิด Dialog
                          Navigator.pushReplacement(
                            context,
                            MaterialPageRoute(
                                builder: (_) =>
                                    AdvertisePage(studentId: _generatedEpId)),
                          );
                        },
                      ),
                    ),
                  ],
                );
              },
            );
          }
        } catch (e) {
          setState(() {
            loading = false;
            error = 'เกิดข้อผิดพลาดในการบันทึกข้อมูล: $e';
          });
        }
      } else {
        setState(() {
          loading = false;
          error = '*รหัส OTP ไม่ถูกต้องหรือหมดอายุ*';
          otpCtrl.clear();
        });
      }
      return;
    }

    // กรณี Login สมาชิกเดิม
    final studentId = studentIdCtrl.text.replaceAll(RegExp(r'\s+'), '');
    final bool isSuccess = await AuthenticationService.login(
      studentId,
      inputOtp,
      expiryTime,
    );
    if (isSuccess && mounted) {
      log("OTP ถูกต้อง เข้าสู่ระบบสำเร็จ");
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(
          builder: (_) {
            return AdvertisePage(studentId: studentId);
          },
        ),
      );
    }

    log('Loading Status $loading');
    if (isSuccess == false) {
      setState(() {
        // ให้เซ็ตตัวแปร loading = false เพื่อที่อนุญาตให้กดปุ่ม Login อีกครั้ง
        loading = false;
        error = '*กรุณากรอกรหัส OTP ให้ถูกต้อง*';
        otpCtrl.clear();
      });
    } else {
      setState(() {
        // เซ็ต loading = true ป้องกันการกดปุ่มซ้ำๆ
        loading = true;
      });
      if (editEmail == true) {
        // ผูกอีเมลที่เลือกเข้ากับรหัสนักศึกษาใน Firestore
        await FirestoreService().updateUser(studentId, {
          'email': newSelectedEmail,
        });
      }
    }
  }

  // ---------- UI Helpers (ไม่กระทบ Logic) ----------
  InputDecoration _inputDecoration({
    required String label,
    IconData? prefixIcon,
    String? hint,
  }) {
    return InputDecoration(
      labelText: label,
      hintText: hint,
      prefixIcon: prefixIcon != null
          ? Icon(prefixIcon, color: const Color(0xFF64748B), size: 22)
          : null,
      filled: true,
      fillColor: const Color(0xFFF8FAFC),
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: Color(0xFFE2E8F0)),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: Color(0xFFE2E8F0)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: Color(0xFF2563EB), width: 1.8),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: Color(0xFFEF4444)),
      ),
      labelStyle: const TextStyle(
        color: Color(0xFF64748B),
        fontWeight: FontWeight.w500,
      ),
    );
  }

  Widget _buildPrimaryButton({
    required String label,
    required IconData icon,
    required VoidCallback? onPressed,
    bool isLoading = false,
  }) {
    return SizedBox(
      width: double.infinity,
      height: 54,
      child: ElevatedButton.icon(
        onPressed: onPressed,
        icon: isLoading
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                  color: Colors.white,
                  strokeWidth: 2.4,
                ),
              )
            : Icon(icon, size: 22),
        label: Text(
          label,
          style: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.3,
          ),
        ),
        style: ElevatedButton.styleFrom(
          backgroundColor: const Color(0xFF2563EB),
          foregroundColor: Colors.white,
          disabledBackgroundColor: const Color(0xFF94A3B8),
          elevation: 0,
          shadowColor: Colors.transparent,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
          ),
        ),
      ),
    );
  }

  Widget _buildSecondaryButton({
    required String label,
    required IconData icon,
    required VoidCallback? onPressed,
    bool isLoading = false,
  }) {
    return SizedBox(
      width: double.infinity,
      height: 50,
      child: OutlinedButton.icon(
        onPressed: onPressed,
        icon: isLoading
            ? const SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(
                  color: Color(0xFF2563EB),
                  strokeWidth: 2.2,
                ),
              )
            : Icon(icon, size: 20),
        label: Text(
          label,
          style: const TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w600,
          ),
        ),
        style: OutlinedButton.styleFrom(
          foregroundColor: const Color(0xFF2563EB),
          side: const BorderSide(color: Color(0xFF2563EB), width: 1.5),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF1F5F9),
      appBar: AppBar(
        elevation: 0,
        backgroundColor: const Color(0xFFF1F5F9),
        surfaceTintColor: Colors.transparent,
        foregroundColor: const Color(0xFF0F172A),
        centerTitle: true,
        title: Text(
          _isOtpSent ? 'ยืนยัน OTP' : 'เข้าสู่ระบบ',
          style: const TextStyle(
            fontWeight: FontWeight.w700,
            fontSize: 24,
            letterSpacing: 0.2,
          ),
        ),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 32),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // ---------- Header Icon + Title ----------
              Center(
                child: Container(
                  width: 120,
                  height: 120,
                  decoration: BoxDecoration(
                    color: _isOtpSent
                        ? const Color(0xFFECFDF5)
                        : const Color(0xFFEFF6FF),
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: (_isOtpSent
                                ? const Color(0xFF10B981)
                                : const Color(0xFF2563EB))
                            .withOpacity(0.12),
                        blurRadius: 24,
                        offset: const Offset(0, 8),
                      ),
                    ],
                  ),
                  child: Icon(
                    _isOtpSent
                        ? Icons.mark_email_read_rounded
                        : Icons.account_circle_rounded,
                    size: 64,
                    color: _isOtpSent
                        ? const Color(0xFF059669)
                        : const Color(0xFF2563EB),
                  ),
                ),
              ),
              const SizedBox(height: 28),

              // ---------- Main Card ----------
              Container(
                padding: const EdgeInsets.all(24),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(20),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withOpacity(0.04),
                      blurRadius: 20,
                      offset: const Offset(0, 6),
                    ),
                  ],
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (_isOtpSent) ...[
                      // OTP Sent Banner
                      Container(
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(
                          color: const Color(0xFFECFDF5),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(
                            color: const Color(0xFFA7F3D0),
                          ),
                        ),
                        child: Column(
                          children: [
                            const Icon(
                              Icons.check_circle_outline_rounded,
                              color: Color(0xFF059669),
                              size: 28,
                            ),
                            const SizedBox(height: 10),
                            const Text(
                              'ส่งรหัส OTP 6 หลักไปที่',
                              style: TextStyle(
                                color: Color(0xFF047857),
                                fontWeight: FontWeight.w500,
                                fontSize: 14,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              _targetEmail,
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                color: Color(0xFF065F46),
                                fontWeight: FontWeight.w700,
                                fontSize: 15,
                              ),
                            ),
                            const SizedBox(height: 6),
                            const Text(
                              'กรุณาตรวจสอบในกล่องจดหมายของคุณ',
                              style: TextStyle(
                                color: Color(0xFF059669),
                                fontSize: 13,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),
                      TextField(
                        controller: otpCtrl,
                        keyboardType: TextInputType.number,
                        maxLength: 6,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 28,
                          letterSpacing: 12,
                          fontWeight: FontWeight.w700,
                          color: Color(0xFF0F172A),
                        ),
                        decoration: _inputDecoration(
                          label: 'รหัส OTP 6 หลัก',
                          prefixIcon: Icons.password_rounded,
                        ).copyWith(
                          counterText: '',
                          contentPadding: const EdgeInsets.symmetric(
                              horizontal: 16, vertical: 18),
                        ),
                      ),
                      const SizedBox(height: 8),
                      TextButton(
                        onPressed: () {
                          setState(() {
                            pickEmail = false;
                            editEmail = false;
                            _isOtpSent = false;
                            otpCtrl.clear();
                            error = '';
                          });
                        },
                        child: const Text(
                          'เปลี่ยนรหัสสมาชิก / เปลี่ยนอีเมล / ขอ OTP ใหม่',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: Color(0xFFEF4444),
                            fontWeight: FontWeight.w600,
                            fontSize: 13.5,
                          ),
                        ),
                      ),
                    ] else ...[
                      // Login Form
                      const Text(
                        'กรอกรหัสสมาชิกเพื่อเข้าสู่ระบบ',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                          color: Color(0xFF0F172A),
                        ),
                      ),
                      const SizedBox(height: 6),
                      const Text(
                        'ใช้รหัสนักศึกษา หรือรหัสบุคคลภายนอก (epXXXX)',
                        style: TextStyle(
                          fontSize: 13,
                          color: Color(0xFF64748B),
                        ),
                      ),
                      const SizedBox(height: 20),
                      TextField(
                        enabled: !_isOtpSent,
                        controller: studentIdCtrl,
                        textAlign: TextAlign.center,
                        maxLength: 15,
                        style: const TextStyle(
                          color: Color(0xFF0F172A),
                          fontSize: 18,
                          letterSpacing: 1.8,
                          fontWeight: FontWeight.w600,
                        ),
                        decoration: _inputDecoration(
                          label: 'รหัสสมาชิก',
                          prefixIcon: Icons.badge_outlined,
                        ).copyWith(counterText: ''),
                      ),
                    ],

                    // ---------- Error Message ----------
                    if (error.isNotEmpty) ...[
                      const SizedBox(height: 16),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 12),
                        decoration: BoxDecoration(
                          color: const Color(0xFFFEF2F2),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: const Color(0xFFFECACA)),
                        ),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(
                              Icons.error_outline_rounded,
                              color: Color(0xFFDC2626),
                              size: 20,
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text(
                                error,
                                style: const TextStyle(
                                  color: Color(0xFFB91C1C),
                                  fontSize: 14,
                                  fontWeight: FontWeight.w500,
                                  height: 1.35,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],

                    const SizedBox(height: 24),

                    // ---------- Action Buttons ----------
                    if (!editEmail || _isOtpSent) ...[
                      _buildPrimaryButton(
                        label: _isOtpSent
                            ? (editEmail
                                ? 'ยืนยัน OTP เพื่อเปลี่ยนอีเมล'
                                : 'ยืนยัน OTP เพื่อเข้าสู่ระบบ')
                            : 'เลือกอีเมลเพื่อรับ OTP',
                        icon: _isOtpSent
                            ? Icons.login_rounded
                            : Icons.email_outlined,
                        isLoading: loading,
                        onPressed: loading
                            ? null
                            : (_isOtpSent
                                ? _verifyOtp
                                : _pickEmailAndSendOtp),
                      ),
                    ],
                    if (!pickEmail && !_isOtpSent) ...[
                      const SizedBox(height: 12),
                      _buildSecondaryButton(
                        label: 'ลงทะเบียนอีเมล(บุคคลภายนอก)',
                        icon: Icons.edit_outlined,
                        isLoading: loading,
                        onPressed: loading ? null : _editEmailAndSendOtp,
                      ),
                    ],
                  ],
                ),
              ),

              const SizedBox(height: 24),
              // Footer
              const Center(
                child: Text(
                  'ระบบยืนยันตัวตนเข้าใช้แอป BLE',
                  style: TextStyle(
                    fontSize: 12,
                    color: Color(0xFF94A3B8),
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}