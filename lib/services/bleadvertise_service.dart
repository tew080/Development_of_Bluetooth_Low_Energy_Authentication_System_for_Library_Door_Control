// นำเข้าเพื่อใช้ MethodChannel
import 'package:flutter/services.dart';
// นำเข้า LogdebugService
import '../services/logdebug_service.dart';
// นำเข้า FirestoreService
import 'firestore_service.dart';
// นำเข้า Flutter Secure Storage สำหรับ cache config ออฟไลน์
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

// คลาสสำหรับจัดการ Bluetooth Low Energy (BLE) ผ่าน Native Code
class BleService {
  // สร้างช่องทางสื่อสาร (Channel) ชื่อ 'ble_advertiser' ให้ตรงกับฝั่ง Android (Native Code)
  static const MethodChannel channel = MethodChannel('ble_advertiser');

  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  // Cache ใน memory เพื่อความเร็ว (ไม่ต้องอ่าน storage ทุกครั้ง)
  static String? _cachedUuid;
  static int? _cachedCompanyId;

  /// โหลด UUID + CompanyID จาก cache หรือ Firestore (ถ้าออนไลน์)
  /// คืนค่า (uuid, companyId) หรือ null ถ้าไม่มีข้อมูลเลย
  static Future<({String uuid, int companyId})?> _loadAdPack({bool forceNetwork = false}) async {
    // 1. ใช้ memory cache ก่อน (เร็วที่สุด)
    if (!forceNetwork && _cachedUuid != null && _cachedCompanyId != null) {
      return (uuid: _cachedUuid!, companyId: _cachedCompanyId!);
    }

    // 2. ลองอ่านจาก Secure Storage (ออฟไลน์ได้)
    try {
      final storedUuid = await _storage.read(key: 'adpack_uuid');
      final storedCid = await _storage.read(key: 'adpack_company_id');
      if (storedUuid != null && storedCid != null) {
        final cid = int.tryParse(storedCid);
        if (cid != null) {
          _cachedUuid = storedUuid;
          _cachedCompanyId = cid;
          log("AdPack loaded from local cache: uuid=$storedUuid cid=$cid");
          // ถ้าไม่ได้บังคับ network ก็ใช้ cache ได้เลย
          if (!forceNetwork) {
            return (uuid: storedUuid, companyId: cid);
          }
        }
      }
    } catch (e) {
      log("Read adpack cache error: $e");
    }

    // 3. ดึงจาก Firestore (ต้องมีเน็ต)
    try {
      final firestoreService = FirestoreService();
      final doc = await firestoreService.getAdpack();
      final String uuid = doc['uuid']?.toString() ?? '';
      final dynamic rawCid = doc['companyID'];
      int companyId;
      if (rawCid is int) {
        companyId = rawCid;
      } else if (rawCid is String) {
        companyId = int.tryParse(rawCid) ??
            (rawCid.startsWith('0x')
                ? int.tryParse(rawCid.substring(2), radix: 16) ?? 0
                : 0);
      } else {
        companyId = 0;
      }

      if (uuid.isNotEmpty && companyId != 0) {
        _cachedUuid = uuid;
        _cachedCompanyId = companyId;
        // บันทึกลง storage เพื่อใช้ตอนออฟไลน์ครั้งหน้า
        await _storage.write(key: 'adpack_uuid', value: uuid);
        await _storage.write(key: 'adpack_company_id', value: companyId.toString());
        log("AdPack fetched from Firestore & cached: uuid=$uuid cid=$companyId");
        return (uuid: uuid, companyId: companyId);
      }
    } catch (e) {
      log("Fetch AdPack from Firestore failed (offline?): $e");
    }

    // 4. ถ้า forceNetwork แล้ว fail แต่มี cache เก่า ใช้ cache เก่า
    if (_cachedUuid != null && _cachedCompanyId != null) {
      return (uuid: _cachedUuid!, companyId: _cachedCompanyId!);
    }

    return null;
  }

  /// เรียกตอนแอปเริ่มหรือตอนมีเน็ต เพื่อ pre-cache config
  static Future<void> preloadAdPack() async {
    await _loadAdPack(forceNetwork: true);
  }

  // รับค่า bleKey (hex payload) ที่ต้องการส่ง
  static Future<void> startAdvertising(String bleKey) async {
    // ดึง config จาก cache ก่อน (เร็ว + ออฟไลน์ได้)
    final adpack = await _loadAdPack();
    if (adpack == null) {
      log("startAdvertising aborted: no AdPack config available");
      return;
    }

    final String uuID = adpack.uuid;
    final int companyID = adpack.companyId;
    const bool devicename = false;
    const bool connectable = false;
    const bool txpowerlevel = false;

    log("Key BLE = $bleKey | uuid=$uuID cid=$companyID");

    if (bleKey.isEmpty) {
      return;
    }

    // ส่งคำสั่งไปยัง Native Android ผ่าน MethodChannel (ไม่รอ network)
    await channel.invokeMethod('startAdvertising', {
      'uuid': uuID,
      'companyId': companyID,
      'data': bleKey,
      'devicename': devicename,
      'connectable': connectable,
      'txpowerlevel': txpowerlevel,
    });
  }

  // ฟังก์ชันสำหรับรอฟัง Callback จาก Native ว่าเริ่มส่งสัญญาณสำเร็จแล้ว
  static void listenAdvertisingStarted(VoidCallback onStarted) {
    channel.setMethodCallHandler((call) async {
      if (call.method == 'onAdvertisingStarted') {
        onStarted();
      }
    });
  }

  static Future<void> stopAdvertising() async {
    try {
      await channel.invokeMethod('stopAdvertising');
      log("Native stopAdvertising called");
    } catch (e) {
      log("stopAdvertising error: $e");
    }
  }
}