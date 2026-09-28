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
// นำเข้า GenerateKeyService สำหรับการสร้างคีย์ + TOTP
import '../services/generatekey_service.dart';
// นำเข้าไลบรารี Flutter Secure Storage เพื่ออ่านหรือจัดเก็บข้อมูลในเครื่อง
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
// นำเข้า NetworkCheckService สำหรับตรวจสอบการเชื่อมต่ออินเทอร์เน็ต
import '../services/networkcheck_service.dart';

class AdvertisePage extends StatefulWidget {
  final String studentId;

  const AdvertisePage({super.key, required this.studentId});

  @override
  State<AdvertisePage> createState() => _AdvertisePageState();
}

class _AdvertisePageState extends State<AdvertisePage> {
  bool advertising = false;
  String currentKey = "";
  String currentOfflineSecret = "";
  bool _isFirstLoad = true;

  // Timer + cancellation flag เพื่อแก้บัคกดหยุดไม่ได้ตอน offline
  Timer? _bleRefreshTimer;
  bool _stopRequested = false; // flag ป้องกัน callback ที่ค้างอยู่ทำงานต่อ
  bool _isStopping = false; // แสดง loading บนปุ่มตอนกำลังหยุด

  // Burst timing (ลดลงเล็กน้อยเพื่อความรวดเร็ว)
  static const Duration _burstOn = Duration(seconds: 4);
  static const Duration _burstOff = Duration(seconds: 3);

  bool checkinoutStatus = false;
  StreamSubscription? _userSubscription;
  StreamSubscription? _userInfoSubscription;
  final FirestoreService firestoreService = FirestoreService();
  String userName = "";
  String userStatus = "";

  static const storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  @override
  void initState() {
    super.initState();
    _subscribeUserInfo();
    // Pre-cache AdPack ตอนเปิดหน้า (ถ้ามีเน็ตจะดึงล่าสุด, ถ้าไม่มีใช้ของเก่า)
    BleService.preloadAdPack();
    BleService.listenAdvertisingStarted(() {
      if (mounted && !_stopRequested) {
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
            final firstName = userInfo['first_name'] ?? '';
            final lastName = userInfo['last_name'] ?? '';
            userName = "$firstName $lastName".trim();

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
    }, onError: (e) {
      // ออฟไลน์ stream error ไม่ต้อง crash
      log("UserInfo stream error (offline?): $e");
    });
  }

  Future<void> _generateBleKey() async {
    String? onlineKey = await storage.read(key: 'my_secret_key');
    String? offlineSecret = await storage.read(key: 'my_offline_secret');

    // สร้างเฉพาะเมื่อยังไม่มีในเครื่อง (ไม่สนเน็ต)
    if (onlineKey == null || onlineKey.isEmpty) {
      onlineKey = generateKey(8, "key");
      await storage.write(key: 'my_secret_key', value: onlineKey);
      log('Created new online key (local): $onlineKey');
    }
    if (offlineSecret == null || offlineSecret.isEmpty) {
      // ใช้ 32 hex ให้อินโทรปีสูง (เดิม 10 สั้นเกินไป)
      offlineSecret = generateKey(32, "key");
      await storage.write(key: 'my_offline_secret', value: offlineSecret);
      log('Created new offline_secret (local): ${offlineSecret.substring(0, 8)}...');
    }

    // อัป Firestore เฉพาะตอนมีเน็ต — fail แล้วไม่ลบของในเครื่อง
    try {
      final hasNet = await NetworkService.onConnectivityChanged.first
          .timeout(const Duration(milliseconds: 800), onTimeout: () => false);
      if (hasNet) {
        await FirestoreService().updateUser(widget.studentId, {
          'key': onlineKey,
          'offline_secret': offlineSecret,
        });
        log('Synced both keys to Firestore');
      } else {
        log('Offline → keep local keys, skip Firestore sync');
      }
    } catch (e) {
      log('updateUser keys failed (offline?): $e');
    }

    // ตั้ง UI เสมอ ไม่ว่าเน็ตจะมีหรือไม่
    _autoStart(onlineKey, offlineSecret);
  }

  void _autoStart(String onlineKey, String offlineSecret) {
    if (mounted) {
      setState(() {
        currentKey = onlineKey;
        currentOfflineSecret = offlineSecret;
      });
    }
    if (_isFirstLoad) {
      _isFirstLoad = false;
      startBurstAdvertising(onlineKey, offlineSecret);
    }
  }

  /// สร้าง Dynamic Payload ณ เวลาปัจจุบัน (เร็ว ไม่รอ network)
  Future<String> _buildPayload(String onlineKey, String offlineSecret) async {
    // ใช้ offline_secret + TOTP เสมอ
    // เหตุผล: มือถือมีเน็ต ≠ ประตูมีเน็ต
    // ถ้าส่ง static key (0x01) ตอนประตูออฟไลน์ → NO MATCH แน่นอน
    final String secretForPayload =
        offlineSecret.isNotEmpty ? offlineSecret : onlineKey;

    final String payload = TotpService.generateDynamicPayload(
      secretKey: secretForPayload,
      isOffline: true, // ← บังคับ TOTP เสมอ
    );
    log("Payload | forceOfflineTOTP=true | $payload");
    return payload;
  }
  
  Future<void> startBurstAdvertising(String onlineKey, String offlineSecret) async {
    if (onlineKey.isEmpty) {
      log("startBurstAdvertising: key empty → abort");
      return;
    }

    _stopRequested = false;
    _isStopping = false;

    bool hasNet = false;
    try {
      hasNet = await NetworkService.onConnectivityChanged.first
          .timeout(const Duration(milliseconds: 800), onTimeout: () => false);
    } catch (e) {
      hasNet = false;
    }

    if (hasNet) {
      log("Online → subscribe CheckinoutStatus");
      _userSubscription?.cancel();
      _userSubscription = FirestoreService()
          .getUserStream(widget.studentId)
          .listen((snapshot) async {
        if (_stopRequested) return;
        if (snapshot.exists) {
          final data = snapshot.data() as Map<String, dynamic>?;
          checkinoutStatus = data?['checkinoutStatus'] ?? false;
          if (checkinoutStatus == true) {
            log("Checked in via Stream → Stopping BLE");
            await stop();
          }
        }
      }, onError: (e) {
        log("Checkinout stream error: $e");
      });
    }

    _bleRefreshTimer?.cancel();
    _bleRefreshTimer = null;

    // ← ส่ง 2 ตัว
    final String firstPayload = await _buildPayload(onlineKey, offlineSecret);
    if (_stopRequested) return;

    await BleService.startAdvertising(firstPayload);
    log("StartAdvertising | $firstPayload");

    if (mounted && !_stopRequested) {
      setState(() {
        advertising = true;
      });
    }

    _bleRefreshTimer = Timer.periodic(_burstOn + _burstOff, (timer) async {
      if (_stopRequested || !timer.isActive) {
        return;
      }

      try {
        if (hasNet && checkinoutStatus == true) {
          log("CheckinoutStatus=true → stop");
          await stop();
          return;
        }

        // ← ส่ง 2 ตัว
        final String payload = await _buildPayload(onlineKey, offlineSecret);
        if (_stopRequested || !timer.isActive) return;

        await BleService.startAdvertising(payload);
        log("Burst Start | $payload");

        if (!hasNet) {
          await Future.delayed(_burstOn);
          if (_stopRequested || !timer.isActive) return;
          await BleService.stopAdvertising();
          log("Burst Stop (offline)");
        }
      } catch (e) {
        log("Timer advertising error: $e");
      }
    });
  }

  /// หยุดการทำงานทันที
  /// - อัปเดต UI + ยกเลิก timer แบบ sync ก่อน (รู้สึกว่าหยุดทันที)
  /// - สั่ง Native stop แบบ fire-and-forget
  /// - Firestore อัปเดตเบื้องหลัง ไม่บล็อกปุ่ม
  Future<void> stop() async {
    if (_isStopping && !advertising) return;

    // 1) ตั้ง flag + ยกเลิก timer/subscription ทันที (sync)
    _stopRequested = true;
    _bleRefreshTimer?.cancel();
    _bleRefreshTimer = null;
    _userSubscription?.cancel();
    _userSubscription = null;
    log("Stop RefreshTimer");

    // 2) อัปเดต UI ทันที → ปุ่มเปลี่ยนเป็น loading แล้วเป็นหยุด
    if (mounted) {
      setState(() {
        _isStopping = true;
        advertising = false;
      });
    }

    // 3) สั่ง Native หยุดทันที (ไม่รอ Firestore)
    //    เรียกซ้ำสั้น ๆ เผื่อ native ยัง busy จาก startAdvertising
    try {
      await BleService.stopAdvertising();
    } catch (e) {
      log("stopAdvertising #1: $e");
    }
    // ยิงรอบสองแบบไม่บล็อกนาน
    Future.microtask(() async {
      try {
        await BleService.stopAdvertising();
        log("Stop Advertising (forced #2)");
      } catch (_) {}
    });

    // 4) Firestore เบื้องหลัง (timeout สั้น, ไม่กระทบ UI)
    Future(() async {
      try {
        await FirestoreService().updateUser(widget.studentId, {
          'checkinoutStatus': false,
        }).timeout(const Duration(milliseconds: 1500));
      } catch (e) {
        log("update checkinoutStatus failed (offline?): $e");
      }
    });

    // 5) ปิด loading บนปุ่ม
    if (mounted) {
      setState(() {
        _isStopping = false;
        advertising = false;
      });
    }
    log("State Advertising = false");
  }

  Future<void> logout() async {
    _stopRequested = true;
    _bleRefreshTimer?.cancel();
    _bleRefreshTimer = null;
    _userSubscription?.cancel();
    _userInfoSubscription?.cancel();

    await BleService.stopAdvertising();
    log("Stop Advertising (logout)");

    try {
      await FirestoreService().updateUser(widget.studentId, {
        'loginStatus': false,
        'checkinoutStatus': false,
      }).timeout(const Duration(seconds: 2));
    } catch (e) {
      log("logout updateUser failed: $e");
    }

    await storage.deleteAll();

    if (mounted) {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (context) => const LoginPage()),
      );
    }
  }

  @override
  void dispose() {
    _stopRequested = true;
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
        backgroundColor: const Color(0xFFF1F5F9),
        surfaceTintColor: Colors.transparent,
        foregroundColor: const Color(0xFF0F172A),
        centerTitle: false,
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'สวัสดีคุณ',
              style: TextStyle(
                fontSize: 18,
                color: Color(0xFF64748B),
                fontWeight: FontWeight.w500,
              ),
            ),
            Text(
              userName.isEmpty ? '...' : userName,
              style: const TextStyle(
                fontSize: 24,
                fontWeight: FontWeight.w700,
                color: Color(0xFF0F172A),
              ),
            ),
          ],
        ),
        actions: [
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
                          onPressed: () => Navigator.pop(context),
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
                            Navigator.pop(context);
                            logout();
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
                Text(
                  _isStopping
                      ? 'กำลังหยุดสัญญาณ'
                      : (isActive ? 'กำลังส่งสัญญาณ' : 'ยังไม่เริ่มทำงาน'),
                  style: TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                    color: _isStopping
                        ? const Color(0xFF94A3B8)
                        : (isActive
                            ? const Color(0xFF2563EB)
                            : const Color(0xFF64748B)),
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  _isStopping
                      ? 'กรุณารอสักครู่...'
                      : (isActive
                          ? 'อุปกรณ์พร้อมให้ระบบสแกน'
                          : 'กดปุ่มด้านล่างเพื่อเริ่มส่งสัญญาณ'),
                  style: const TextStyle(
                    fontSize: 14,
                    color: Color(0xFF94A3B8),
                  ),
                ),
                const SizedBox(height: 28),
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
                                  'รหัสยืนยันตัวตน',
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
                                        : const Color(0xFF0F172A),
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
                SizedBox(
                  width: double.infinity,
                  height: 56,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: _isStopping
                          ? const Color(0xFF94A3B8)
                          : (isActive
                              ? const Color(0xFFDC2626)
                              : const Color(0xFF2563EB)),
                      foregroundColor: Colors.white,
                      elevation: 0,
                      shadowColor: Colors.transparent,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(16),
                      ),
                    ),
                    // ตอนกำลังหยุด ปิดการกดซ้ำ
                    onPressed: _isStopping
                        ? null
                        : () {
                            if (advertising) {
                              stop();
                            } else {
                              startBurstAdvertising(currentKey, currentOfflineSecret);
                            }
                          },
                    child: _isStopping
                        ? const Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              SizedBox(
                                width: 22,
                                height: 22,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2.5,
                                  valueColor: AlwaysStoppedAnimation<Color>(
                                    Colors.white,
                                  ),
                                ),
                              ),
                              SizedBox(width: 12),
                              Text(
                                'กำลังหยุด...',
                                style: TextStyle(
                                  fontSize: 17,
                                  fontWeight: FontWeight.w700,
                                  letterSpacing: 0.3,
                                ),
                              ),
                            ],
                          )
                        : Row(
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