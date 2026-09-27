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
  StreamSubscription? _userInfoSubscription;
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
    _subscribeUserInfo();
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

  void _subscribeUserInfo() {
    _userInfoSubscription = firestoreService
        .getUserStream(widget.studentId)
        .listen((snapshot) {
      if (snapshot.exists && snapshot.data() != null) {
        final userInfo = snapshot.data() as Map<String, dynamic>;
        
        if (mounted) {
          setState(() {
            // อัปเดตชื่อ
            final firstName = userInfo['first_name'] ?? '';
            final lastName = userInfo['last_name'] ?? '';
            userName = "$firstName $lastName".trim();

            // อัปเดตสถานะเช็กอิน/เอาต์
            final rawStatus = userInfo['last_status']?.toString();
            if (rawStatus == "Clock-IN") {
              userStatus = "เช็กอินแล้ว";
            } else if (rawStatus == "Clock-OUT") {
              userStatus = "เช็กเอาต์แล้ว";
            } else {
              userStatus = "ไม่ทราบสถานะ";
            }
          });
        }
      }
    });
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

    if (!hasNet) {
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
    _userInfoSubscription?.cancel();
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
    _userInfoSubscription?.cancel();
    BleService.stopAdvertising();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isActive = advertising;

    return Scaffold(
      backgroundColor: const Color(0xFFF1F5F9),
      appBar: AppBar(
        elevation: 0,
        backgroundColor: Colors.white,
        foregroundColor: const Color(0xFF0F172A),
        centerTitle: false,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'สวัสดี',
              style: TextStyle(
                fontSize: 13,
                color: Color(0xFF64748B),
                fontWeight: FontWeight.w500,
              ),
            ),
            Text(
              userName.isEmpty ? '...' : userName,
              style: const TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w700,
                color: Color(0xFF0F172A),
              ),
            ),
          ],
        ),
        actions: [
          // ปุ่ม Logout มุมขวาบน
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: IconButton(
              icon: Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: const Color(0xFFF1F5F9),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Icon(
                  Icons.logout_rounded,
                  size: 22,
                  color: Color(0xFF64748B),
                ),
              ),
              tooltip: 'ออกจากระบบ',
              onPressed: () {
                // แสดง Dialog ยืนยันการออก
                showDialog(
                  context: context,
                  builder: (context) {
                    return AlertDialog(
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(20),
                      ),
                      title: const Text(
                        'ยืนยันการออกจากระบบ',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontWeight: FontWeight.w700,
                          fontSize: 18,
                        ),
                      ),
                      content: const Text(
                        'คุณต้องการหยุดส่งสัญญาณและออกจากระบบหรือไม่?',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 15,
                          color: Color(0xFF475569),
                          height: 1.4,
                        ),
                      ),
                      actionsAlignment: MainAxisAlignment.center,
                      actions: [
                        TextButton(
                          onPressed: () {
                            Navigator.pop(context); // ปิด Dialog
                          },
                          style: TextButton.styleFrom(
                            foregroundColor: const Color(0xFF64748B),
                            padding: const EdgeInsets.symmetric(
                                horizontal: 20, vertical: 12),
                          ),
                          child: const Text(
                            'ยกเลิก',
                            style: TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                        const SizedBox(width: 8),
                        ElevatedButton(
                          onPressed: () {
                            Navigator.pop(context); // ปิด Dialog
                            logout(); // เรียกฟังก์ชัน Logout
                          },
                          style: ElevatedButton.styleFrom(
                            backgroundColor: const Color(0xFFDC2626),
                            foregroundColor: Colors.white,
                            elevation: 0,
                            padding: const EdgeInsets.symmetric(
                                horizontal: 24, vertical: 12),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                          child: const Text(
                            'ออกจากระบบ',
                            style: TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                      ],
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                // ---------- Status Circle ----------
                Container(
                  width: 160,
                  height: 160,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: isActive
                        ? const Color(0xFFEFF6FF)
                        : const Color(0xFFF1F5F9),
                    boxShadow: [
                      BoxShadow(
                        color: (isActive
                                ? const Color(0xFF2563EB)
                                : const Color(0xFF94A3B8))
                            .withOpacity(0.18),
                        blurRadius: 32,
                        offset: const Offset(0, 12),
                      ),
                    ],
                  ),
                  child: Icon(
                    isActive
                        ? Icons.bluetooth_connected_rounded
                        : Icons.bluetooth_disabled_rounded,
                    size: 80,
                    color: isActive
                        ? const Color(0xFF2563EB)
                        : const Color(0xFF94A3B8),
                  ),
                ),
                const SizedBox(height: 28),

                // ---------- Status Label ----------
                Text(
                  isActive ? 'กำลังส่งสัญญาณ' : 'ยังไม่เริ่มทำงาน',
                  style: TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                    color: isActive
                        ? const Color(0xFF2563EB)
                        : const Color(0xFF64748B),
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  isActive
                      ? 'อุปกรณ์พร้อมให้ระบบสแกน'
                      : 'กดปุ่มด้านล่างเพื่อเริ่มส่งสัญญาณ',
                  style: const TextStyle(
                    fontSize: 14,
                    color: Color(0xFF94A3B8),
                  ),
                ),
                const SizedBox(height: 28),

                // ---------- Info Cards ----------
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(18),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withOpacity(0.04),
                        blurRadius: 16,
                        offset: const Offset(0, 4),
                      ),
                    ],
                  ),
                  child: Column(
                    children: [
                      // Key Row
                      Row(
                        children: [
                          Container(
                            padding: const EdgeInsets.all(10),
                            decoration: BoxDecoration(
                              color: const Color(0xFFEFF6FF),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: const Icon(
                              Icons.key_rounded,
                              color: Color(0xFF2563EB),
                              size: 22,
                            ),
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text(
                                  'คีย์ BLE',
                                  style: TextStyle(
                                    fontSize: 13,
                                    color: Color(0xFF64748B),
                                    fontWeight: FontWeight.w500,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  currentKey.isEmpty ? '—' : currentKey,
                                  style: const TextStyle(
                                    fontSize: 18,
                                    fontWeight: FontWeight.w700,
                                    color: Color(0xFF0F172A),
                                    letterSpacing: 1.2,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 16),
                        child: Divider(height: 1, color: Color(0xFFE2E8F0)),
                      ),
                      // Status Row
                      Row(
                        children: [
                          Container(
                            padding: const EdgeInsets.all(10),
                            decoration: BoxDecoration(
                              color: userStatus == "เช็กอินแล้ว"
                                  ? const Color(0xFFECFDF5)
                                  : const Color(0xFFFFF7ED),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Icon(
                              userStatus == "เช็กอินแล้ว"
                                  ? Icons.check_circle_rounded
                                  : Icons.access_time_rounded,
                              color: userStatus == "เช็กอินแล้ว"
                                  ? const Color(0xFF059669)
                                  : const Color(0xFFEA580C),
                              size: 22,
                            ),
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text(
                                  'สถานะเช็กอิน/เอาต์',
                                  style: TextStyle(
                                    fontSize: 13,
                                    color: Color(0xFF64748B),
                                    fontWeight: FontWeight.w500,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  userStatus.isEmpty ? '—' : userStatus,
                                  style: TextStyle(
                                    fontSize: 16,
                                    fontWeight: FontWeight.w700,
                                    color: userStatus == "เช็กอินแล้ว"
                                        ? const Color(0xFF059669)
                                        : const Color(0xFF0F172A)  ,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 32),

                // ---------- Action Button ----------
                SizedBox(
                  width: double.infinity,
                  height: 56,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: isActive
                          ? const Color(0xFFDC2626)
                          : const Color(0xFF2563EB),
                      foregroundColor: Colors.white,
                      elevation: 0,
                      shadowColor: Colors.transparent,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(16),
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
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                          isActive
                              ? Icons.stop_circle_rounded
                              : Icons.play_circle_rounded,
                          size: 26,
                        ),
                        const SizedBox(width: 10),
                        Text(
                          isActive ? 'หยุดส่งสัญญาณ' : 'เริ่มส่งสัญญาณ',
                          style: const TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 0.3,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Text(
                  'รหัสสมาชิก: ${widget.studentId}',
                  style: const TextStyle(
                    fontSize: 13,
                    color: Color(0xFF94A3B8),
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}