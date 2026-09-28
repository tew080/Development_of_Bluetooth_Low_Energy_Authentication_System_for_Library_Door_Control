# ble_scanner.py
import asyncio
import hashlib
import hmac
import time
from datetime import datetime

from bleak import BleakScanner

import shared_state
from config import Config
from db_manager import init_firebase, sync_record_attendance
from logger import log, log_event


def print_packet_details(device, advertisement_data, raw_data=None, flag=None, data4=None):
    """แสดงรายละเอียดแพ็กเก็ตที่รับได้ใน console"""
    log("=" * 60)
    log("📦 RECEIVED ADVERTISEMENT PACKET")
    log("=" * 60)
    log(f"  Device Address  : {device.address}")
    log(f"  Device Name     : {device.name if device.name else 'Unknown'}")
    log(f"  RSSI            : {advertisement_data.rssi} dBm")

    uuids = [str(u).lower() for u in (advertisement_data.service_uuids or [])]
    log(f"  Service UUIDs   : {uuids if uuids else '-'}")

    if advertisement_data.manufacturer_data:
        for comp_id, data in advertisement_data.manufacturer_data.items():
            hex_data = data.hex()
            log(
                f"  Manufacturer    : ID=0x{comp_id:04X}  "
                f"len={len(data)}  hex={hex_data}"
            )
            # แสดงทีละ byte เพื่อ debug ง่าย
            byte_str = " ".join(f"{b:02X}" for b in data)
            log(f"  Raw Bytes       : [{byte_str}]")
    else:
        log("  Manufacturer    : (none)")

    if raw_data is not None:
        log(f"  Payload len     : {len(raw_data)} bytes")
        if flag is not None:
            flag_name = {0x01: "ONLINE", 0x02: "OFFLINE"}.get(flag, f"UNKNOWN(0x{flag:02X})")
            log(f"  Flag            : 0x{flag:02X} ({flag_name})")
        if data4 is not None:
            log(f"  Data (4 bytes)  : {data4.hex()}")

    log("-" * 60)


def generate_totp_hash(secret_key: str, time_step_seconds: int = 30, offset: int = 0) -> bytes:
    """
    คำนวณ 4-byte TOTP hash ให้ตรงกับฝั่ง Flutter (TotpService)
    HMAC-SHA256(utf8(secret_key), big-endian-int64(time_block + offset))
    แล้วตัดเอา 4 bytes แรก
    """
    epoch_seconds = int(time.time())
    time_block = (epoch_seconds // time_step_seconds) + offset
    time_bytes = time_block.to_bytes(8, byteorder="big", signed=False)
    key_bytes = secret_key.encode("utf-8")
    digest = hmac.new(key_bytes, time_bytes, hashlib.sha256).digest()
    return digest[:4]


async def activate_door_unlock(device, lookup_key, user_info):
    shared_state.is_processing = True

    doc_id = user_info["doc_id"]
    full_name = f"คุณ {user_info['first_name']} {user_info['last_name']}".strip()

    today_date = datetime.now().strftime("%Y-%m-%d")
    if user_info.get("last_update_date") != today_date:
        show_status = "Clock-IN"
    else:
        show_status = (
            "Clock-IN" if user_info.get("last_status") == "Clock-OUT" else "Clock-OUT"
        )

    shared_state.gui_user_name = full_name
    if show_status == "Clock-IN":
        shared_state.gui_action_text = "ยินดีต้อนรับ"
    else:
        shared_state.gui_action_text = "เดินทางปลอดภัย"

    shared_state.gui_light_state = "green"

    log(f"🔓 UNLOCK → {full_name} | {show_status} | doc_id={doc_id}")

    # ส่งเข้าคิว / เขียน Firestore ทันที (db_manager จัดการ offline queue เอง)
    asyncio.create_task(asyncio.to_thread(sync_record_attendance, doc_id))
    await asyncio.sleep(Config.UNLOCK_DELAY)

    shared_state.gui_light_state = "red"
    shared_state.gui_user_name = ""
    shared_state.gui_action_text = ""
    shared_state.is_processing = False


def ble_detection_callback(device, advertisement_data):
    target_uuid = getattr(Config, "TARGET_UUID", None)
    if shared_state.is_processing or target_uuid is None:
        return

    uuids = [str(u).lower() for u in advertisement_data.service_uuids]
    if Config.TARGET_UUID not in uuids:
        return
    if advertisement_data.rssi < Config.RSSI_THRESHOLD:
        return

    raw_data = advertisement_data.manufacturer_data.get(Config.COMPANY_ID)
    if not raw_data:
        return

    try:
        # ---------------------------------------------------------------
        # โครงสร้าง Payload: [Flag 1 byte] + [Data 4 bytes] = 5 bytes
        # Flag 0x01 = Online  → Data = Static Key (4 bytes)
        # Flag 0x02 = Offline → Data = TOTP HMAC hash (4 bytes)
        # รองรับ payload เก่า (ไม่มี flag) ด้วย
        # ---------------------------------------------------------------
        if len(raw_data) < 4:
            log(f"⚠ Payload สั้นเกินไป ({len(raw_data)} bytes) จาก {device.address}")
            return

        now = datetime.now()
        matched_secret = None
        user_info = None
        mode = "unknown"
        flag = None
        data4 = None

        if len(raw_data) >= 5:
            flag = raw_data[0]
            data4 = raw_data[1:5]

            # แสดงแพ็กเก็ตที่รับได้
            print_packet_details(device, advertisement_data, raw_data, flag, data4)

            if flag == 0x01:
                # ===== Online Mode =====
                static_key_hex = data4.hex()
                mode = "online"
                log(f"🔍 [ONLINE] Lookup static key: {static_key_hex}")

                if static_key_hex in shared_state.valid_keys:
                    matched_secret = static_key_hex
                    user_info = shared_state.valid_keys[static_key_hex]
                    log(f"✅ [ONLINE] MATCH → key={static_key_hex} | user={user_info.get('doc_id')}")
                else:
                    log(f"❌ [ONLINE] NO MATCH → key={static_key_hex} ไม่พบใน valid_keys "
                        f"(มี {len(shared_state.valid_keys)} keys)")

            elif flag == 0x02:
                mode = "offline"
                log(f"🔍 [OFFLINE] TOTP hash จากแพ็กเก็ต: {data4.hex()}")
                log(f"   กำลังเทียบกับ {len(shared_state.valid_keys)} secrets (window ±1)...")

                checked = 0
                for secret_key, info in shared_state.valid_keys.items():
                    # ใช้ offline_secret ถ้ามี ไม่งั้น fallback เป็น key เดิม (รองรับสมาชิกเก่า)
                    totp_base = info.get("offline_secret") or secret_key
                    for offset in (-1, 0, 1):
                        expected = generate_totp_hash(totp_base, offset=offset)
                        checked += 1
                        if expected == data4:
                            matched_secret = secret_key
                            user_info = info
                            log(
                                f"✅ [OFFLINE] MATCH → offline_secret={totp_base[:8]}... "
                                f"| offset={offset:+d} | hash={expected.hex()} "
                                f"| user={info.get('doc_id')}"
                            )
                            break
                    if matched_secret is not None:
                        break

                if matched_secret is None:
                    log(f"❌ [OFFLINE] NO MATCH หลังเทียบ {checked} hashes "
                        f"(secrets={len(shared_state.valid_keys)})")

            else:
                log(f"⚠ Flag ไม่รู้จัก: 0x{flag:02X} — ลองตีความเป็น legacy")
                print_packet_details(device, advertisement_data, raw_data, flag, data4)

        # ----- Backward compatibility: payload เก่าที่ไม่มี Flag -----
        if matched_secret is None and len(raw_data) == 4:
            legacy_hex = raw_data.hex()
            mode = "legacy"
            print_packet_details(device, advertisement_data, raw_data)
            log(f"🔍 [LEGACY] Lookup key: {legacy_hex}")

            if legacy_hex in shared_state.valid_keys:
                matched_secret = legacy_hex
                user_info = shared_state.valid_keys[legacy_hex]
                log(f"✅ [LEGACY] MATCH → key={legacy_hex} | user={user_info.get('doc_id')}")
            else:
                log(f"❌ [LEGACY] NO MATCH → key={legacy_hex}")

        if matched_secret is None or user_info is None:
            return

        # ----- Cooldown ป้องกัน Replay Attack -----
        if matched_secret in shared_state.last_scanned_times:
            elapsed = (now - shared_state.last_scanned_times[matched_secret]).total_seconds()
            if elapsed < Config.COOLDOWN_SECONDS:
                log(
                    f"⏳ COOLDOWN → key={matched_secret[:8]}... "
                    f"เหลือ {Config.COOLDOWN_SECONDS - elapsed:.1f}s "
                    f"(ข้ามไม่ปลดล็อก)"
                )
                return

        shared_state.last_scanned_times[matched_secret] = now

        log(
            f"🎯 FINAL MATCH ({mode.upper()}) → key={matched_secret[:8]}... "
            f"| user={user_info.get('doc_id')} "
            f"| name={user_info.get('first_name', '')} {user_info.get('last_name', '')}"
        )
        asyncio.create_task(activate_door_unlock(device, matched_secret, user_info))

    except Exception as e:
        log(f"- Error in ble_detection_callback: {e}")


async def scan_loop():
    log("- STARTING ACCESS CONTROL SYSTEM -")
    init_firebase()
    await asyncio.sleep(2)

    scanner = BleakScanner(ble_detection_callback)
    await scanner.start()
    log("- Listening for signals...")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        await scanner.stop()
        log("🔴 System Shutdown.")


def run_background_scanner():
    asyncio.run(scan_loop())