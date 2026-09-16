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

  String targetID = 'ep';
  // รับค่าจาก รหัสนักศึกษาจาก TextField
  final studentIdCtrl = TextEditingController();
  // รับค่าจาก รหัสนักศึกษาจาก TextField
  final otpCtrl = TextEditingController();

  Future<void> _pickEmailAndSendOtp() async {
    GoogleSignInAccount? account;
    final studentId = studentIdCtrl.text.replaceAll(RegExp(r'\s+'), '');
    final userCheck = await firestoreService.getUser(studentId);
    emaillCheck = userCheck['email'];

    if (!userCheck.exists) {
      setState(() {
        error = '*ไม่พบข้อมูลผู้ใช้*';
        studentIdCtrl.clear();
      });
      // จบการทำงานทันทีถ้าข้อมูลว่าง
    } else if (emaillCheck.isEmpty) {
      setState(() {
        error = '*ไม่พบอีเมล*';
        studentIdCtrl.clear();
      });
      // จบการทำงานทันทีถ้าข้อมูลว่าง
    }

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
        .add(const Duration(seconds: 30))
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
                  <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 30 วินาที*</p>
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

  Future<void> _editEmailAndSendOtp() async {
    GoogleSignInAccount? account;
    final studentId = studentIdCtrl.text.replaceAll(RegExp(r'\s+'), '');
    final userCheck = await firestoreService.getUser(studentId);
    emaillCheck = userCheck['email'];

    if (!userCheck.exists) {
      setState(() {
        error = '*ไม่พบข้อมูลผู้ใช้*';
        studentIdCtrl.clear();
      });
      // จบการทำงานทันทีถ้าข้อมูลว่าง
    } else if (emaillCheck.isEmpty) {
      setState(() {
        error = '*ไม่พบอีเมล*';
      });
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
        error = '*กรุณาใช้รหัสผู้ใช้งานสำหรับบุคคลภายนอก (เช่น epXXXXX) เท่านั้น*';
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
        .add(const Duration(seconds: 30))
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
                  <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 30 วินาที*</p>
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
                  <p style="color: red; font-weight: bold;">*รหัสนี้มีอายุการใช้งาน 30 วินาที*</p>
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
      // ตั้งค่าอีเมลเป้าหมาย
      _targetEmail = selectedEmail;
      // ปิดสถานะการโหลด
      loading = false;
    });

    // ป้องกันการค้างของ Session
    await _googleSignIn.signOut();
  }

  Future<void> _verifyOtp() async {
    final studentId = studentIdCtrl.text.replaceAll(RegExp(r'\s+'), '');
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_isOtpSent ? 'ยืนยัน OTP' : 'เข้าสู่ระบบ')),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(64),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                _isOtpSent ? Icons.mark_email_read : Icons.account_circle,
                size: 200,
                color: _isOtpSent ? Colors.green : Colors.blue,
              ),
              const SizedBox(height: 30),
              if (_isOtpSent) ...[
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Colors.green.shade50,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.green.shade200),
                  ),
                  child: Text(
                    'ส่งรหัส OTP 6 หลักไปที่เมล\n$_targetEmail\nตรวจสอบในกล่องจดหมายของคุณ',
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Colors.green,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 20),
              // TextField (), สร้างช่องรับค่าข้อมูล
              TextField(
                // ให้ TextField รับค่าจาก _isOtpSent
                enabled: !_isOtpSent,
                // ให้ TextField รับค่าจาก studentIdCtrl
                controller: studentIdCtrl,
                textAlign: TextAlign.center,
                maxLength: 15,
                // รับค่าเป็นตัวเลขเท่านั้น
                //keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'รหัสสมาชิก',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.person, color: Colors.grey),
                ),
                style: const TextStyle(
                  color: Colors.black,
                  // กำหนดขนาดของตัวอักษร
                  fontSize: 18,
                  // กำหนดระยะห่างระหว่างตัวอักษร
                  letterSpacing: 1.5,
                ),
              ),
              const SizedBox(height: 20),

              if (_isOtpSent) ...[
                TextField(
                  controller: otpCtrl,
                  // รับค่าเป็นตัวเลขเท่านั้น
                  keyboardType: TextInputType.number,
                  maxLength: 6,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 24, letterSpacing: 8),
                  decoration: const InputDecoration(
                    labelText: 'รหัส OTP 6 หลัก',
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.password_sharp, color: Colors.grey),
                  ),
                ),
                TextButton(
                  child: const Text(
                    'เปลี่ยนรหัสนักศึกษา / เปลี่ยนอีเมล / ขอotpใหม่',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.red),
                  ),
                  onPressed: () {
                    setState(() {
                      pickEmail = false;
                      editEmail = false;
                      _isOtpSent = false;
                      otpCtrl.clear();
                      error = '';
                    });
                  },
                ),
              ],
              // ถ้า ค่าในตัวแปล error ไม่เป็นค่าว่าง แสดงว่ามีข้อผิดพลาด จาก setState();
              if (error.isNotEmpty)
                Text(
                  error,
                  style: const TextStyle(
                    color: Colors.red,
                    // กำหนดขนาดของตัวอักษร
                    fontSize: 16,
                    // กำหนดความหนาของตัวอักษร
                    fontWeight: FontWeight.bold,
                    // กำหนดระยะห่างระหว่างตัวอักษร
                    letterSpacing: 1,
                  ),
                  textAlign: TextAlign.center,
                ),
              const SizedBox(height: 10),
              if (!editEmail) ...[
                ElevatedButton.icon(
                  label: Text(
                    _isOtpSent
                        ? 'ยืนยัน OTP เพื่อเข้าสู่ระบบ'
                        : 'เลือกอีเมลเพื่อรับOTP',
                    style: const TextStyle(fontSize: 15),
                  ),
                  onPressed: loading
                      ? null
                      : (_isOtpSent ? _verifyOtp : _pickEmailAndSendOtp),
                  icon: loading
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            color: Colors.red,
                            strokeWidth: 2,
                          ),
                        )
                      : Icon(_isOtpSent ? Icons.login : Icons.email),
                  // กำหนดสไตล์ของปุ่ม
                  style: ElevatedButton.styleFrom(
                    // สีพื้นหลังของปุ่ม
                    backgroundColor: Colors.blue,
                    // สีเบื้องหน้าของปุ่ม
                    foregroundColor: Colors.white,
                    // กำหนดขนาดของปุ่ม
                    minimumSize: Size(200, 50),
                    shape: RoundedRectangleBorder(
                      // กำหนดขอบของปุ่ม
                      //side: BorderSide(color: Colors.indigoAccent, width: 2),
                      // กำหนดความโค้งของขอบปุ่ม
                      borderRadius: BorderRadius.circular(30.0),
                    ), //End RoundedRectangleBorder
                  ), //End ElevatedButton.styleFrom
                ), // End ElevatedButton
              ],

              if (!pickEmail) ...[
                const SizedBox(height: 10),
                ElevatedButton.icon(
                  label: Text(
                    _isOtpSent
                        ? 'ยืนยัน OTP เพื่อเข้าสู่ระบบ'
                        : 'ผูกหรือเปลี่ยนอีเมล',
                    style: const TextStyle(fontSize: 15),
                  ),
                  onPressed: loading
                      ? null
                      : (_isOtpSent ? _verifyOtp : _editEmailAndSendOtp),
                  icon: loading
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            color: Colors.red,
                            strokeWidth: 2,
                          ),
                        )
                      : Icon(_isOtpSent ? Icons.login : Icons.email),
                  // กำหนดสไตล์ของปุ่ม
                  style: ElevatedButton.styleFrom(
                    // สีพื้นหลังของปุ่ม
                    backgroundColor: Colors.blue,
                    // สีเบื้องหน้าของปุ่ม
                    foregroundColor: Colors.white,
                    // กำหนดขนาดของปุ่ม
                    minimumSize: Size(200, 50),
                    shape: RoundedRectangleBorder(
                      // กำหนดขอบของปุ่ม
                      //side: BorderSide(color: Colors.indigoAccent, width: 2),
                      // กำหนดความโค้งของขอบปุ่ม
                      borderRadius: BorderRadius.circular(30.0),
                    ), //End RoundedRectangleBorder
                  ), //End ElevatedButton.styleFrom
                ), // End ElevatedButton
              ],
            ], // End Row
          ), // End Column
        ), // End Container
      ), // End SingleChildScrollView
    ); // End Scaffold
  } // End Widget build
} // End Class LoginPage
