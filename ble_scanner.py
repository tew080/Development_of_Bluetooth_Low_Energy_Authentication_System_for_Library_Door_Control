# ble_scanner.py
import asyncio
from datetime import datetime

from bleak import BleakScanner

import shared_state
from config import Config
from db_manager import init_firebase, sync_record_attendance
from logger import log, log_event


def print_packet_details(device, advertisement_data):
    log_event("=" * 50)
    log_event("RECEIVED ADVERTISEMENT PACKET")
    log_event("=" * 50)
    log_event(f"Device Address  : {device.address}")
    log_event(f"Device Name     : {device.name if device.name else 'Unknown'}")
    log_event(f"RSSI            : {advertisement_data.rssi} dBm")

    if advertisement_data.manufacturer_data:
        for comp_id, data in advertisement_data.manufacturer_data.items():
            hex_data = data.hex()
            log_event(
                f"Manufacturer Data: [ID: 0x{comp_id:04X}] [Hex: 0x{hex_data}] [Hex: {data}] "
            )
    log_event("-" * 50)


async def activate_door_unlock(device, hex_key, user_info):
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

    # 🔥 ปรับปรุง: การตั้งค่า checkinoutStatus = True ถูกรวมเข้าไปทำใน sync_record_attendance 
    # ทำให้ยุบเหลือ Firestore Write เพียงครั้งเดียว (ประหยัด Write Limit)
    asyncio.create_task(asyncio.to_thread(sync_record_attendance, doc_id))
    await asyncio.sleep(Config.UNLOCK_DELAY)

    shared_state.gui_light_state = "red"
    shared_state.gui_user_name = ""
    shared_state.gui_action_text = ""
    shared_state.is_processing = False


def ble_detection_callback(device, advertisement_data):
    target_uuid = getattr(Config, 'TARGET_UUID', None)
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
        hex_key = raw_data.hex()
        
        # 🔥 เพิ่มการเช็ก Cooldown ของคีย์นี้
        now = datetime.now()
        if hex_key in shared_state.last_scanned_times:
            elapsed = (now - shared_state.last_scanned_times[hex_key]).total_seconds()
            if elapsed < Config.COOLDOWN_SECONDS:
                # ยังอยู่ในช่วง Cooldown ห้ามสแกนซ้ำ
                return

        if hex_key in shared_state.valid_keys:
            # อัปเดตเวลาสแกนล่าสุดของคีย์นี้
            shared_state.last_scanned_times[hex_key] = now

            user_info = shared_state.valid_keys[hex_key]
            asyncio.create_task(activate_door_unlock(device, hex_key, user_info))
    except Exception as e:
        log(f"- Error: {e}")


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