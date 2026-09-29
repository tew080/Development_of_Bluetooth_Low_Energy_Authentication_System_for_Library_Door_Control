import 'package:flutter/material.dart';
import 'pages/login_page.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../pages/bleadvertise_page.dart';
import '../services/logdebug_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  const storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  await Firebase.initializeApp();

  // ถ้ามี session อยู่แล้ว → ไปหน้า Advertise (secret หมุนเฉพาะตอน login ใหม่)
  final String? studentIDCheck = await storage.read(key: 'student_id');
  log('session student_id=${studentIDCheck ?? "(none)"}');

  runApp(MyApp(startPage: studentIDCheck));
}

class MyApp extends StatelessWidget {
  final String? startPage;
  const MyApp({super.key, this.startPage});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      theme: ThemeData(
        primarySwatch: Colors.blue,
        fontFamily: 'Google Sans',
      ),
      home: startPage != null
          ? AdvertisePage(studentId: startPage!)
          : const LoginPage(),
    );
  }
}