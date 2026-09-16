// นำเข้าเพื่อใช้ Timer และ StreamSubscription
import 'dart:async';
// นำเข้า Material UI
import 'package:flutter/material.dart';
// นำเข้า FirestoreService
import '../services/firestore_service.dart';
// นำเข้าหน้า Login
import 'login_page.dart';
// นำเข้า bleadvertise service
import '../services/bleadvertise_service.dart';
// นำเข้า LogdebugService
import '../services/logdebug_service.dart';
// นำเข้า GenerateKeyService สำหรับการสร้างคีย์
import '../services/generatekey_service.dart';
// นำเข้าไลบรารี Flutter Secure Storage เพื่ออ่านหรือจัดเก็บข้อมูลในเครื่อง
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
// นำเข้า NetworkCheckService สำหรับตรวจสอบการเชื่อมต่ออินเทอร์เน็ต
import '../services/networkcheck_service.dart';

class AdvertisePage extends StatefulWidget {
  // รับรหัสนักศึกษาเข้ามา
  final String studentId;

  const AdvertisePage({super.key, required this.studentId});

  @override
  State<AdvertisePage> createState() {
    return _AdvertisePageState();
  }
}

class _AdvertisePageState extends State<AdvertisePage> {
  // สถานะว่ากำลังส่งสัญญาณอยู่หรือไม่
  bool advertising = false;
  // เก็บ Key ปัจจุบัน
  String currentKey = "";
  // ตัวแปรเช็คว่าเป็นครั้งแรกที่โหลดหรือไม่ (สำหรับ Auto Start)
  bool _isFirstLoad = true;
  // Timer สำหรับ Burst Mode
  Timer? _bleRefreshTimer;
  // เวลาเปิดสัญญาณ (5 วินาที)
  static const Duration _burstOn = Duration(seconds: 5);
  // เวลาพักสัญญาณ (4 วินาที)
  static const Duration _burstOff = Duration(seconds: 4);
  // ตัวแปรเช็คสถานะ Checkinout จาก Firestore
  bool checkinoutStatus = false;
  // ตัวจัดการการดักฟังข้อมูล Firestore
  StreamSubscription? _userSubscription;
  // สร้าง Instance ของ FirestoreService เพื่อใช้งาน
  final FirestoreService firestoreService = FirestoreService();
  // ตัวแปรเก็บชื่อผู้ใช้
  String userName = "";
  // ตัวแปรเก็บสถานะผู้ใช้
  String userStatus = "";

  // สร้าง FlutterSecureStorage เพื่ออ่านข้อมูลที่จัดเก็บในเครื่อง
  static const storage = FlutterSecureStorage(
    // encryptedSharedPreferences = true เพื่อเข้ารหัสข้อมูลที่จัดเก็บในเครื่อง
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  @override
  void initState() {
    _getUserInfo();
    super.initState();
    // ดักฟัง Callback จาก Native เมื่อเริ่มส่งสัญญาณสำเร็จ
    BleService.listenAdvertisingStarted(() {
      // ถ้าหน้าจอยังแสดงอยู่ ให้เปลี่ยนสถานะ advertising เป็น true
      if (mounted) {
        setState(() {
          advertising = true;
        });
      }
    });
    _generateBleKey();
  }

  Future<void> _getUserInfo() async {
    final userInfo = await firestoreService.getUser(widget.studentId);
      userName = userInfo['first_name'].toString() + " " + userInfo['last_name'].toString();
      userStatus = userInfo['last_status'].toString();
      if (userStatus == "Clock-IN") {
        userStatus = "เช็กอินแล้ว";
      } else if (userStatus == "Clock-OUT") {
        userStatus = "เช็กเอาต์แล้ว";
      } else {
        userStatus = "ไม่ทราบสถานะ";
      }
  }

  Future<void> _generateBleKey() async {
    String? storedKey = await storage.read(key: 'my_secret_key');
    String newKey = "";
    // มีของเก่าใช้ของเก่า / ไม่มีให้สร้างใหม่
    if (storedKey != null) {
      newKey = storedKey;
      log('Found existing key: $newKey');
    } else {
      log('Creating new key...');
      newKey = generateKey(8, "key");
      await storage.write(key: 'my_secret_key', value: newKey);
      await FirestoreService().updateUser(widget.studentId, {'key': newKey});
      log('newKey :$newKey');
    }
    // ถ้า Key ว่างเปล่า ให้จบการทำงาน
    if (newKey.isEmpty) {
      log('ไม่พบ key');
      return;
    }
    _autoStart(newKey);
  }

  // ข้อมูล User แบบ Real-time
  void _autoStart(String newKey) async {
    // อัปเดต Key ในหน้าจอ
    if (mounted) {
      setState(() {
        currentKey = newKey;
      });
    }
    // ตรวจสอบ Logic Auto Start
    if (_isFirstLoad) {
      _isFirstLoad = false;
      startBurstAdvertising(newKey);
    }
  }

  // เริ่มส่งสัญญาณแบบ Burst Mode (เปิด-ปิด สลับกัน)
  Future<void> startBurstAdvertising(String key) async {
    bool hasNet = await NetworkService.onConnectivityChanged.first;
    if (hasNet) {
        log("มีการเชื่อมต่ออินเทอร์เน็ตอยู่ -> ตรวจสอบ CheckinoutStatus จาก Firestore");
      // ยกเลิก Subscription เก่า (ถ้ามี) เพื่อป้องกันการฟังซ้ำ
      _userSubscription?.cancel();
      // ตรวจสอบการอัปเดตแบบ Real-time จาก Firestore
      _userSubscription = FirestoreService()
          .getUserStream(widget.studentId)
          .listen((snapshot) async {
        
        // ตรวจสอบว่ามีเอกสารอยู่จริงและแปลง (Cast) ข้อมูลให้เป็น Map
        if (snapshot.exists) {
          final data = snapshot.data() as Map<String, dynamic>?;
          checkinoutStatus = data?['checkinoutStatus'];

          // สั่งหยุดส่งทันทีเมื่อเช็คอินแล้ว
          if (checkinoutStatus == true) {
            log("User checked in via Stream -> Stopping BLE immediately");
            await stop();
          }
        }
      });
    }
    // ยกเลิก Timer ตัวเก่าก่อน (ป้องกันการทำงานซ้อน)
    _bleRefreshTimer?.cancel();
    log("StopRefreshTimer");

    if(!hasNet) {
      log("**** StartBurstAdvertising ****");
      log("ไม่มีการเชื่อมต่ออินเทอร์เน็ตอยู่");

      // สั่งเริ่มส่งสัญญาณครั้งแรกทันที
      await BleService.startAdvertising(key);
      log("Auto StartAdvertising");

      // อัปเดตสถานะ UI
      if (mounted) {
        setState(() {
          advertising = true;
        });
      }
      log("State Advertising: $advertising");

      // ตั้ง Timer ให้ทำงานวนลูป
      _bleRefreshTimer = Timer.periodic(_burstOn + _burstOff, (timer) async {
        log("[DEBUG] Reset BLE: $timer");

        // สั่งเริ่มส่ง
        await BleService.startAdvertising(key);
        log("Start Advertising");

        // รอเวลาพัก
        await Future.delayed(_burstOff);
        log("Delayed: $_burstOff");

        // สั่งหยุดส่ง
        await BleService.stopAdvertising();
        log("Stop Advertising");
      });
    } else if (hasNet) {
      log("**** StartBurstAdvertising ****");
      log("มีการเชื่อมต่ออินเทอร์เน็ตอยู่");

      // สั่งเริ่มส่งสัญญาณครั้งแรกทันที
      await BleService.startAdvertising(key);
      log("Auto StartAdvertising");

      // อัปเดตสถานะ UI
      if (mounted) {
        setState(() {
          advertising = true;
        });
      }
      log("State Advertising: $advertising");

      // ตั้ง Timer ให้ทำงานวนลูป
      _bleRefreshTimer = Timer.periodic(_burstOn + _burstOff, (timer) async {
        log("CheckinoutStatus: $checkinoutStatus");

        // สั่งเริ่มส่ง
        await BleService.startAdvertising(key);
        log("Start Advertising");

        // รอเวลาพัก
        await Future.delayed(_burstOff);
        log("Delayed: $_burstOff");

        // สั่งหยุดส่ง
        //await BleService.stopAdvertising();
        //log("Stop Advertising");
      });
    }
  }

  // ฟังก์ชันหยุดการทำงาน (Manual Stop)
  Future<void> stop() async {
    // ยกเลิก Timer
    _bleRefreshTimer?.cancel();
    log("Stop RefreshTimer");

    await FirestoreService().updateUser(widget.studentId, {
      'checkinoutStatus': false
    });

    _userSubscription?.cancel();

    // สั่งหยุด BLE
    await BleService.stopAdvertising();
    log("Stop Advertising");

    // อัปเดตสถานะ UI
    if (mounted) {
      setState(() {
        advertising = false;
      });
    }
    log("State Advertising = $advertising");
  }

  Future<void> logout() async {
    _bleRefreshTimer?.cancel();
    _userSubscription?.cancel();
    // หยุดส่งสัญญาณ Bluetooth ทันที (สำคัญมาก)
    await BleService.stopAdvertising();
    log("Stop Advertising");

    await FirestoreService().updateUser(widget.studentId, {
      'loginStatus': false,
      'checkinoutStatus': false,
    });

    // ลบข้อมูลทั้งหมดใน FlutterSecureStorage
    await storage.deleteAll();

    // กลับไปหน้า Login และล้าง Stack เดิมทิ้ง (กด Back กลับมาไม่ได้)
    if (mounted) {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (context) => const LoginPage()),
      );
    }
  }

  @override
  void dispose() {
    // คืนทรัพยากรเมื่อปิดหน้านี้
    // ป้องกัน Memory Leak แต่สำหรับการ Logout จะจัดการใน func logout() อีกที
    _bleRefreshTimer?.cancel();
    _userSubscription?.cancel();
    BleService.stopAdvertising();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('สวัสดีคุณ: $userName'),
        actions: [
          // ปุ่ม Logout มุมขวาบน
          IconButton(
            icon: const Icon(Icons.logout, size: 40, color: Colors.grey),
            tooltip: 'ออกจากระบบ',
            onPressed: () {
              // แสดง Dialog ยืนยันการออก
              showDialog(
                context: context,
                builder: (context) {
                  return AlertDialog(
                    title: const Text(
                      'ยืนยันการออก',
                      textAlign: TextAlign.center,
                    ),
                    content: const Text(
                      'คุณต้องการหยุดส่งสัญญาณและออกจากระบบหรือไม่?',
                    ),
                    actions: [
                      TextButton(
                        onPressed: () {
                          Navigator.pop(context); // ปิด Dialog
                        },
                        child: const Text('ยกเลิก'),
                      ),
                      TextButton(
                        onPressed: () {
                          Navigator.pop(context); // ปิด Dialog
                          logout(); // เรียกฟังก์ชัน Logout
                        },
                        child: const Text(
                          'ออกจากระบบ',
                          style: TextStyle(color: Colors.red),
                        ),
                      ),
                    ],
                  );
                },
              );
            },
          ),
        ],
      ),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // เช็คสถานะการส่งสัญญาณเพื่อแสดง UI ที่เหมาะสม
            if (advertising) ...[
              const Icon(
                Icons.bluetooth_connected,
                size: 200,
                color: Colors.blueAccent,
              ),
              const SizedBox(height: 20),
              // แสดง Key
              Text(
                'กำลังส่งสัญญาณ\nคีย์: $currentKey\nสถานะ: $userStatus',
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 18, color: Colors.green),
              ),
              const SizedBox(height: 10),
            ] else ...[
              // แสดงไอคอนหยุด
              const Icon(
                Icons.bluetooth_disabled,
                size: 200,
                color: Colors.grey,
              ),
              const SizedBox(height: 20),
              Text(
                'ยังไม่เริ่มทำงาน\nคีย์: $currentKey\nสถานะ: $userStatus',
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 18),
              ),
            ],
            const SizedBox(height: 10),
            // ปุ่ม Start/Stop
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: advertising ? Colors.red : Colors.blue,
                padding: const EdgeInsets.symmetric(
                  horizontal: 30,
                  vertical: 15,
                ),
              ),
              onPressed: () {
                // สลับสถานะการทำงาน
                if (advertising) {
                  stop();
                } else {
                  startBurstAdvertising(currentKey);
                }
              },
              child: Text(
                advertising ? 'หยุดส่งสัญญาณ' : 'เริ่มส่งสัญญาณ',
                style: const TextStyle(fontSize: 19, color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
