# db_manager.py
import csv
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

import firebase_admin
import requests
from firebase_admin import credentials as fb_credentials
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

import shared_state
from config import Config
from dashboard import update_dashboard_data_file, notify_members_updated, notify_new_data_available
from logger import log

import os
import json

# หมายเหตุ: init_firebase() และ on_snapshot_update() ตัวจริงที่ใช้งานอยู่ท้ายไฟล์นี้
# (มี load/save local cache เพิ่มเข้ามา) เวอร์ชันแรกที่เคยอยู่ตรงนี้ถูกลบออกแล้ว
# เพราะ Python จะใช้ def ตัวหลังสุดอยู่แล้ว การมี 2 ตัวซ้ำมีแต่จะทำให้สับสนตอนแก้โค้ดต่อ


def fetch_ble_config():
    try:
        config_ref = (
            shared_state.db.collection(Config.COLLECTION_CONFIG)
            .document("advertisingPackage")
            .get()
        )
        if config_ref.exists:
            data = config_ref.to_dict()
            Config.TARGET_UUID = str(data.get("uuid", "")).lower()
            comp_id = data.get("companyID")
            if isinstance(comp_id, str):
                Config.COMPANY_ID = int(comp_id, 16)
            else:
                Config.COMPANY_ID = int(comp_id)
            log(
                f"- Config Loaded -> UUID: {Config.TARGET_UUID}, CompanyID: {hex(Config.COMPANY_ID)}"
            )
    except Exception as e:
        log(f"❌ Fetch Config Error: {e}")


def sync_record_attendance(doc_id):
    now = datetime.now()
    today_date = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    # 🔥 OPTIMIZATION 1: ดึงข้อมูลผู้ใช้งานจาก In-Memory valid_keys แทนการเรียก member_ref.get() (0 Reads!)
    member_info = None
    for k, v in shared_state.valid_keys.items():
        if v.get("doc_id") == doc_id or v.get("member_id") == doc_id:
            member_info = v
            break

    if member_info:
        prefix = member_info.get("prefix", "")
        first_name = member_info.get("first_name", "ไม่ระบุ")
        last_name = member_info.get("last_name", "")
        faculty = member_info.get("faculty", "ไม่ระบุ")
        branch = member_info.get("branch", "ไม่ระบุ")
        last_status = member_info.get("last_status", "Clock-OUT")
        last_update_date = member_info.get("last_update_date", "")
    else:
        # Fallback กรณีไม่มีใน RAM memory จริงๆ
        member_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(doc_id)
        member_doc = member_ref.get()
        if member_doc.exists:
            member_data = member_doc.to_dict()
            prefix = member_data.get("prefix", "")
            first_name = member_data.get("first_name", "ไม่ระบุ")
            last_name = member_data.get("last_name", "")
            faculty = member_data.get("faculty", "ไม่ระบุ")
            branch = member_data.get("branch", "ไม่ระบุ")
            last_status = member_data.get("last_status", "Clock-OUT")
            last_update_date = member_data.get("last_update_date", "")
        else:
            prefix, first_name, last_name, faculty, branch, last_status, last_update_date = "", "ไม่ระบุ", "", "ไม่ระบุ", "ไม่ระบุ", "Clock-OUT", ""

    is_first_visit_today = False
    if last_update_date != today_date:
        new_status = "Clock-IN"
        is_first_visit_today = True
    else:
        new_status = "Clock-IN" if last_status == "Clock-OUT" else "Clock-OUT"

    # 🔥 OPTIMIZATION 2: รวมการตั้งค่า checkinoutStatus = True ไว้ใน Write เดียวกัน
    member_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(doc_id)
    member_ref.set(
        {
            "last_status": new_status,
            "last_update_date": today_date,
            "last_update_time": time_str,
            "checkinoutStatus": True
        },
        merge=True
    )

    # บันทึก Attendance Log (1 Write)
    log_doc_id = f"{today_date}_{time_str.replace(':', '')}_{doc_id}"
    log_ref = shared_state.db.collection(Config.COLLECTION_ATTENDANCE).document(log_doc_id)

    new_log_event = {
        "member_id": doc_id,
        "prefix": prefix,
        "first_name": first_name,
        "last_name": last_name,
        "faculty": faculty,
        "branch": branch,
        "date": today_date,
        "time": time_str,
        "action": new_status,
        "is_first_visit": is_first_visit_today,
    }
    log_ref.set(new_log_event)

    log(f"- Firebase Updated: [{new_status}] User: {doc_id} (checkinoutStatus: True)")

    # 🔥 OPTIMIZATION 3: อัปเดตไฟล์แดชบอร์ดโดยการส่ง new_log_event เข้า Local Cache (0 Reads!)
    update_dashboard_data_file(new_event=new_log_event)


# นำโค้ดนี้ไปแทนที่ฟังก์ชันเดิมใน db_manager.py

def import_csv_to_firebase():
    if shared_state.db is None:
        messagebox.showerror("Error", "Firebase is not connected yet. Please wait.")
        return

    file_path = filedialog.askopenfilename(
        title="นำเข้าไฟล์ CSV",
        filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")),
    )

    if not file_path:
        return

    try:
        # 🔥 OPTIMIZATION 1: เอาการโหลด .stream() ที่ดึงข้อมูลทั้ง Collection ออกทั้งหมด (0 Reads!)
        # ใช้การตั้งค่า merge=True ของ Firestore ในการจัดการ อัปเดต/สร้างใหม่ อัตโนมัติ

        with open(file_path, mode="r", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            headers = [str(h).strip() for h in reader.fieldnames if h]
            id_column = "member_id" if "member_id" in headers else "student_id"
            if id_column not in headers:
                messagebox.showerror("Format Error", "CSV must contain a 'member_id' column.")
                return

            batch = shared_state.db.batch()
            count_processed, count_updated = 0, 0
            duplicate_count, operations_in_batch = 0, 0
            seen_doc_ids = set()
            pending_refs = []

            for row in reader:
                clean_row = {}
                for k, v in row.items():
                    if k:
                        clean_key = str(k).strip()
                        clean_val = str(v).strip() if v else ""
                        if clean_val.startswith('="') and clean_val.endswith('"'):
                            clean_val = clean_val[2:-1]
                        elif clean_val in ['=""', '""', '=']:
                            clean_val = ""
                        clean_row[clean_key] = clean_val

                doc_id = clean_row.get(id_column, "")
                if not doc_id:
                    continue
                if doc_id in seen_doc_ids:
                    duplicate_count += 1
                    continue
                seen_doc_ids.add(doc_id)

                student_data = {}
                for key, val_str in clean_row.items():
                    if key in {"member_id", "student_id"}:
                        continue
                    if key in ["current_otp", "otp_expiry"]:
                        student_data[key] = int(val_str) if val_str.isdigit() else 0
                    elif key == "loginStatus":
                        student_data[key] = True if val_str.lower() == "true" else False
                    else:
                        if str(val_str).strip() != "":
                            student_data[key] = val_str

                if not student_data:
                    continue

                doc_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(doc_id)
                # ใช้ merge=True เพื่อให้ Firestore ทับเฉพาะฟิลด์ใหม่โดยไม่ลบฟิลด์เดิม ไม่ต้องโหลดข้อมูลมาเช็คก่อน
                batch.set(doc_ref, student_data, merge=True)
                pending_refs.append(doc_ref)
                
                count_processed += 1
                operations_in_batch += 1

                if operations_in_batch >= 400:
                    count_updated += sum(
                        doc.exists for doc in shared_state.db.get_all(pending_refs)
                    )
                    batch.commit()
                    batch = shared_state.db.batch()
                    operations_in_batch = 0
                    pending_refs = []

            if operations_in_batch > 0:
                count_updated += sum(
                    doc.exists for doc in shared_state.db.get_all(pending_refs)
                )
                batch.commit()

            count_added = count_processed - count_updated
            if count_processed > 0:
                notify_members_updated()  # บังคับรีเฟรช cache สมาชิก dashboard_data.js ทันที
            messagebox.showinfo(
                "Import Summary",
                f"- เพิ่มข้อมูลใหม่: {count_added} รายการ\n"
                f"- อัปเดตข้อมูลเดิม: {count_updated} รายการ\n"
                f"- ข้อมูลซ้ำไม่เพิ่ม: {duplicate_count} รายการ"
            )

    except Exception as e:
        log(f"❌ CSV Import Error: {e}")
        messagebox.showerror("Import Error", f"- นำเข้าข้อมูลจาก CSV ไม่สำเร็จ:\n{str(e)}")


def import_attendance_csv_to_firebase():
    if shared_state.db is None:
        messagebox.showerror("Error", "Firebase is not connected yet. Please wait.")
        return

    file_path = filedialog.askopenfilename(
        title="นำเข้าไฟล์ CSV (Attendance Logs)",
        filetypes=(("CSV Files", "*.csv"), ("All Files", "*.*")),
    )

    if not file_path:
        return

    try:
        # 🔥 OPTIMIZATION 2: ไม่ดึง History ทั้งหมดด้วย .stream() มาเช็คซ้ำแล้ว ช่วยลดเวลาได้มหาศาล
        with open(file_path, mode="r", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            headers = [str(h).strip() for h in reader.fieldnames if h]
            id_column = "member_id" if "member_id" in headers else "student_id"
            required_cols = {id_column, "date", "time"}
            if not required_cols.issubset(set(headers)):
                messagebox.showerror("Format Error", "CSV must contain 'member_id', 'date' and 'time' columns.")
                return

            batch = shared_state.db.batch()
            count_processed, count_updated = 0, 0
            duplicate_count, operations_in_batch = 0, 0
            seen_doc_ids = set()
            pending_refs = []

            for row in reader:
                clean_row = {}
                for k, v in row.items():
                    if k:
                        clean_key = str(k).strip()
                        clean_val = str(v).strip() if v else ""
                        if clean_val.startswith('="') and clean_val.endswith('"'):
                            clean_val = clean_val[2:-1]
                        elif clean_val in ['=""', '""', '=']:
                            clean_val = ""
                        clean_row[clean_key] = clean_val

                member_id = clean_row.get(id_column, "")
                date_str = clean_row.get("date", "")
                time_str = clean_row.get("time", "")

                if not member_id or not date_str or not time_str:
                    continue

                doc_id = clean_row.get("doc_id", "").strip()
                if not doc_id:
                    doc_id = f"{date_str}_{time_str.replace(':', '')}_{member_id}"
                if doc_id in seen_doc_ids:
                    duplicate_count += 1
                    continue
                seen_doc_ids.add(doc_id)

                log_data = {"member_id": member_id}
                for key, val_str in clean_row.items():
                    if key in {"doc_id", "member_id", "student_id"}:
                        continue
                    if key == "is_first_visit":
                        log_data[key] = val_str.strip().lower() == "true"
                    else:
                        log_data[key] = val_str

                doc_ref = shared_state.db.collection(Config.COLLECTION_ATTENDANCE).document(doc_id)
                batch.set(doc_ref, log_data, merge=True)
                pending_refs.append(doc_ref)
                
                count_processed += 1
                operations_in_batch += 1

                if operations_in_batch >= 400:
                    count_updated += sum(
                        doc.exists for doc in shared_state.db.get_all(pending_refs)
                    )
                    batch.commit()
                    batch = shared_state.db.batch()
                    operations_in_batch = 0
                    pending_refs = []

            if operations_in_batch > 0:
                count_updated += sum(
                    doc.exists for doc in shared_state.db.get_all(pending_refs)
                )
                batch.commit()

            count_added = count_processed - count_updated
            if count_processed > 0:
                notify_new_data_available()  # บังคับรีเฟรช cache attendance dashboard_data.js ทันที
            messagebox.showinfo(
                "Import Summary",
                f"- เพิ่มข้อมูลใหม่: {count_added} รายการ\n"
                f"- อัปเดตข้อมูลเดิม: {count_updated} รายการ\n"
                f"- ข้อมูลซ้ำไม่เพิ่ม: {duplicate_count} รายการ"
            )

    except Exception as e:
        log(f"❌ Attendance Log CSV Import Error: {e}")
        messagebox.showerror("Import Error", f"- นำเข้าข้อมูล Attendance Logs จาก CSV ไม่สำเร็จ:\n{str(e)}")

def get_member_by_id(member_id):
    if shared_state.db is None:
        return None
    try:
        doc_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(member_id)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        log(f"❌ Error fetching member: {e}")
        return None

def update_member_data(member_id, update_data):
    if shared_state.db is None:
        return False
    try:
        doc_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(member_id)
        doc_ref.set(update_data, merge=True)
        notify_members_updated()  # บังคับรีเฟรช cache สมาชิก dashboard_data.js ทันที
        log(f"- Successfully updated member ID: {member_id}")
        return True
    except Exception as e:
        log(f"❌ Error updating member: {e}")
        return False

# --- ฟังก์ชันจัดการ Collection admin ---
def get_admin_email_config():
    if shared_state.db is None:
        return None
    try:
        doc = shared_state.db.collection("admin").document("emailAdmin").get()
        if doc.exists:
            return doc.to_dict()
        return {}
    except Exception as e:
        log(f"❌ Error fetching admin email config: {e}")
        return None

def update_admin_email_config(email, email_app_password):
    if shared_state.db is None:
        return False
    try:
        shared_state.db.collection("admin").document("emailAdmin").set({
            "email": email.strip(),
            "emailAppPassword": email_app_password.strip()
        }, merge=True)
        log("- Successfully updated admin email config")
        return True
    except Exception as e:
        log(f"❌ Error updating admin email config: {e}")
        return False

# --- ฟังก์ชันจัดการ Collection connect ---
def get_ble_connect_config():
    if shared_state.db is None:
        return None
    try:
        doc = shared_state.db.collection("connect").document("advertisingPackage").get()
        if doc.exists:
            return doc.to_dict()
        return {}
    except Exception as e:
        log(f"❌ Error fetching BLE connect config: {e}")
        return None

def update_ble_connect_config(company_id, uuid_str):
    if shared_state.db is None:
        return False
    try:
        comp_id_val = int(company_id) if str(company_id).isdigit() else company_id
        shared_state.db.collection("connect").document("advertisingPackage").set({
            "companyID": comp_id_val,
            "uuid": uuid_str.lower().strip()
        }, merge=True)
        fetch_ble_config()  # โหลดการตั้งค่าเข้า Config ใหม่ทันที
        log("- Successfully updated BLE connect config")
        return True
    except Exception as e:
        log(f"❌ Error updating BLE connect config: {e}")
        return False
    
def add_external_person(prefix, first_name, last_name, email):
    """ฟังก์ชันเพิ่มข้อมูลบุคคลภายนอกเข้าสู่ Firestore Collection 'student'"""
    if shared_state.db is None:
        return False, "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"

    try:
        # นับจำนวนบุคคลภายนอกเดิมเพื่อสร้าง ID รันอัตโนมัติ (เช่น ep0001)
        docs = shared_state.db.collection(Config.COLLECTION_MEMBER)\
            .where(filter=FieldFilter("faculty", "==", "บุคคลภายนอก")).stream()
        count = sum(1 for _ in docs)
        new_id = f"ep{count + 1:04d}"

        external_data = {
            "member_id": new_id,
            "prefix": prefix.strip(),
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "email": email.strip(),
            "faculty": "บุคคลภายนอก",
            "branch": "",
            Config.FIELD_NAME: "",  # คีย์ (key) ปล่อยว่างไว้
            "loginStatus": False,
            "last_status": "Clock-OUT",
            "last_update_date": "",
            "last_update_time": ""
        }

        # บันทึกลง Firestore โดยใช้ new_id เป็น Document ID
        doc_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(new_id)
        doc_ref.set(external_data, merge=True)
        
        notify_members_updated()  # บังคับรีเฟรช cache สมาชิก dashboard_data.js ทันที
        log(f"- เพิ่มข้อมูลบุคคลภายนอกสำเร็จ ID: {new_id}")
        return True, new_id
    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการเพิ่มบุคคลภายนอก: {e}")
        return False, str(e)

def load_local_users_cache():
    """โหลดข้อมูลผู้ใช้งานจากไฟล์แคชก่อนเชื่อมต่อฐานข้อมูล"""
    os.makedirs(Config.CACHE_DIR, exist_ok=True)
    if os.path.exists(Config.USERS_CACHE_FILE):
        try:
            with open(Config.USERS_CACHE_FILE, 'r', encoding='utf-8') as f:
                shared_state.valid_keys = json.load(f)
            log(f"- Loaded {len(shared_state.valid_keys)} users from local cache.")
        except Exception as e:
            log(f"❌ Error loading users cache: {e}")
            shared_state.valid_keys = {}
    else:
        shared_state.valid_keys = {}

def save_local_users_cache():
    """บันทึกข้อมูลผู้ใช้งานล่าสุดลงไฟล์แคช"""
    os.makedirs(Config.CACHE_DIR, exist_ok=True)
    try:
        with open(Config.USERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(shared_state.valid_keys, f, ensure_ascii=False, indent=4)
    except Exception as e:
        log(f"❌ Error saving users cache: {e}")

def _periodic_dashboard_refresh(interval_seconds=60):
    """เธรดพื้นหลังที่คอยเรียก update_dashboard_data_file() เป็นระยะ ๆ ตลอดที่แอปทำงาน

    เหตุผล: notify_members_updated()/notify_new_data_available() ช่วยให้แคชอัปเดต
    "ทันที" เฉพาะตอนข้อมูลถูกเขียนผ่านฟังก์ชันในไฟล์นี้เท่านั้น (import CSV, เพิ่ม/แก้
    สมาชิก ฯลฯ) แต่ถ้ามีใครไปเขียนข้อมูลลง Firestore ผ่านทางอื่น (แก้ตรงใน Firebase
    Console, สคริปต์ภายนอก, ฟีเจอร์ใหม่ในอนาคตที่ลืมเรียก notify_*) จะไม่มีอะไรไป
    กระตุ้นให้แคชรีเฟรชเลย เธรดนี้จึงเป็น safety net ที่ทำให้ไม่ว่าใครจะเขียนข้อมูล
    มาทางไหนก็ตาม แคชจะตามทันภายในเวลาที่คาดการณ์ได้เสมอ

    ปลอดภัยกับโควต้าอ่านของ Firestore เพราะ update_dashboard_data_file() แยก TTL
    เป็น 2 ระดับ (ดู dashboard.py): attendance ใช้ delta query (date >= ล่าสุดที่มี)
    จึงรีเฟรชได้ถี่ทุก ~1 นาทีแบบแทบไม่มีต้นทุนเพิ่มเมื่อไม่มีข้อมูลใหม่จริง ส่วนสมาชิก
    ใช้ .stream() อ่านทั้ง collection (แพงกว่ามากที่ 3,000คน) จึงมี TTL แยกยาวถึง
    MEMBERS_CACHE_EXPIRATION_MINUTES (6 ชม.) เพื่อไม่ให้เธรดนี้ไปอ่านสมาชิกทั้งหมดซ้ำ
    ทุกนาทีจนชนโควต้า
    """
    while True:
        time.sleep(interval_seconds)
        try:
            if shared_state.db is not None:
                update_dashboard_data_file(skip_ai_trigger=True)
        except Exception as e:
            log(f"❌ Periodic Dashboard Refresh Error: {e}")


def init_firebase():
    try:
        load_local_users_cache() # ดึงจากไฟล์แคชก่อน
        if not firebase_admin._apps:
            cred = fb_credentials.Certificate(Config.FB_KEY_PATH)
            firebase_admin.initialize_app(cred)
        shared_state.db = firestore.client()
        fetch_ble_config()
        shared_state.db.collection(Config.COLLECTION_MEMBER).on_snapshot(on_snapshot_update)
        threading.Thread(target=_periodic_dashboard_refresh, daemon=True).start()
        log("- Firebase Connected & Syncing...")
    except Exception as e:
        log(f"❌ Firebase Init Error: {e}")
        exit(1)

def on_snapshot_update(col_snapshot, changes, read_time):
    try:
        if getattr(shared_state, 'valid_keys', None) is None:
            shared_state.valid_keys = {}
            
        has_changes = False

        for change in changes:
            doc = change.document
            data = doc.to_dict()
            key = str(data.get(Config.FIELD_NAME, "")).strip()

            if change.type.name in ['ADDED', 'MODIFIED']:
                keys_to_delete = [k for k, v in shared_state.valid_keys.items() if v.get("doc_id") == doc.id]
                for k in keys_to_delete:
                    del shared_state.valid_keys[k]

                if key:
                    shared_state.valid_keys[key] = {
                        "doc_id": doc.id,
                        "member_id": data.get("member_id") or data.get("student_id", doc.id),
                        "prefix": data.get("prefix", ""),
                        "first_name": data.get("first_name", "ไม่ระบุ"),
                        "last_name": data.get("last_name", ""),
                        "faculty": data.get("faculty", "ไม่ระบุ"),
                        "branch": data.get("branch", "ไม่ระบุ"),
                        "last_status": data.get("last_status", "Clock-OUT"),
                        "last_update_date": data.get("last_update_date", ""),
                        "last_update_time": data.get("last_update_time", ""),
                    }
                has_changes = True
                    
            elif change.type.name == 'REMOVED':
                keys_to_delete = [k for k, v in shared_state.valid_keys.items() if v.get("doc_id") == doc.id]
                for k in keys_to_delete:
                    del shared_state.valid_keys[k]
                has_changes = True

        # หากมีข้อมูลเปลี่ยนแปลง (Delta) ให้อัปเดตไฟล์แคช
        if has_changes:
            save_local_users_cache()
            log(f"- Database Sync: Updated memory & cache. Total active keys: {len(shared_state.valid_keys)}")
        
    except Exception as e:
        log(f"❌ Firebase Update Error: {e}")

# --- ฟังก์ชันลบข้อมูลสมาชิก ---
def _validate_prefix_list(prefix_input):
    """ตรวจสอบรูปแบบรหัส 2 ตัวหน้าที่ผู้ใช้กรอก (เช่น '66,67,68') ก่อนนำไปค้นหา/ลบจริง
    คืนค่า (valid, cleaned_prefixes, error_message)"""
    if not prefix_input or not str(prefix_input).strip():
        return False, [], "กรุณาระบุรหัส 2 ตัวหน้าที่ต้องการลบ"

    raw_parts = [p.strip() for p in str(prefix_input).split(',')]
    raw_parts = [p for p in raw_parts if p]
    if not raw_parts:
        return False, [], "กรุณาระบุรหัส 2 ตัวหน้าที่ต้องการลบ"

    invalid = [p for p in raw_parts if not (p.isdigit() and len(p) == 2)]
    if invalid:
        return False, [], (
            f"รูปแบบรหัสไม่ถูกต้อง: {', '.join(invalid)}\n"
            "ต้องเป็นตัวเลข 2 หลักเท่านั้น (เช่น 66,67,68) คั่นด้วยเครื่องหมายจุลภาค"
        )

    return True, raw_parts, ""


def count_members_by_prefix(prefix_input):
    """นับจำนวนสมาชิกที่ตรงกับรหัส 2 ตัวหน้าที่ระบุ โดยไม่ลบข้อมูลจริง ใช้แสดงให้
    ผู้ดูแลเห็นก่อนตัดสินใจกดยืนยันลบจริงอีกที (ตอบโจทย์ "ก่อนลบบอกด้วยว่ามีเท่าไหร่")"""
    if shared_state.db is None:
        return False, 0, "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"

    valid, prefixes, err = _validate_prefix_list(prefix_input)
    if not valid:
        return False, 0, err

    try:
        docs = shared_state.db.collection(Config.COLLECTION_MEMBER).stream()
        count = 0
        for doc in docs:
            doc_id = doc.id
            data = doc.to_dict() or {}
            member_id = str(data.get("member_id") or data.get("student_id") or doc_id)
            if any(doc_id.startswith(p) or member_id.startswith(p) for p in prefixes):
                count += 1
        return True, count, ""
    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการนับจำนวนสมาชิก: {e}")
        return False, 0, str(e)


# --- ฟังก์ชันลบข้อมูลสมาชิก ---
def delete_members_by_prefix(prefix_input):
    """ลบข้อมูลสมาชิกแบบกลุ่มโดยใช้อักษร/รหัส 2 ตัวหน้า (รองรับหลายรหัส เช่น '66,67,68')
    หมายเหตุ: ตัว UI (main.py) จะเรียก count_members_by_prefix() ก่อนเสมอเพื่อให้ผู้ดูแล
    เห็นจำนวนและยืนยัน แล้วค่อยเรียกฟังก์ชันนี้จริง ๆ ตอนกดยืนยันรอบสุดท้าย"""
    if shared_state.db is None:
        return False, 0, "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"

    valid, prefixes, err = _validate_prefix_list(prefix_input)
    if not valid:
        return False, 0, err

    try:
        docs = shared_state.db.collection(Config.COLLECTION_MEMBER).stream()
        batch = shared_state.db.batch()
        count = 0
        ops_in_batch = 0

        for doc in docs:
            doc_id = doc.id
            data = doc.to_dict() or {}
            member_id = str(data.get("member_id") or data.get("student_id") or doc_id)

            # ตรวจสอบว่า doc_id หรือ member_id ขึ้นต้นด้วย prefix ใดๆ ที่ระบุหรือไม่
            if any(doc_id.startswith(p) or member_id.startswith(p) for p in prefixes):
                batch.delete(doc.reference)
                count += 1
                ops_in_batch += 1

                if ops_in_batch >= 400:
                    batch.commit()
                    batch = shared_state.db.batch()
                    ops_in_batch = 0

        if ops_in_batch > 0:
            batch.commit()

        if count > 0:
            notify_members_updated()
            log(f"- ลบข้อมูลสมาชิกแบบกลุ่มสำเร็จ {count} รายการ (Prefix: {', '.join(prefixes)})")
            return True, count, f"ลบข้อมูลสำเร็จจำนวน {count} รายการ"
        else:
            return True, 0, f"ไม่พบข้อมูลสมาชิกที่ขึ้นต้นด้วย: {', '.join(prefixes)}"

    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการลบข้อมูลแบบกลุ่ม: {e}")
        return False, 0, str(e)


def _validate_member_query(query_text):
    """ตรวจสอบรูปแบบคำค้นหาเบื้องต้นก่อนค้นหา/ลบรายบุคคล (กันช่องว่างล้วน, ยาวเกินไป,
    หรืออักขระแปลกปลอมที่ไม่ควรอยู่ในรหัสสมาชิกหรือชื่อ-นามสกุล)"""
    text = str(query_text).strip()
    if not text:
        return False, "", "กรุณากรอกรหัสสมาชิก หรือ ชื่อ-นามสกุล"
    if len(text) > 100:
        return False, "", "ข้อความยาวเกินไป กรุณากรอกรหัสสมาชิกหรือชื่อ-นามสกุลให้ถูกต้อง"
    if not re.fullmatch(r"[\w\u0E00-\u0E7F\s\-\.]+", text):
        return False, "", "รูปแบบไม่ถูกต้อง กรุณากรอกเฉพาะตัวอักษร ตัวเลข และช่องว่าง"
    return True, text, ""


def find_member_for_deletion(query_text):
    """ค้นหาสมาชิกที่จะลบ (ไม่ลบจริง) คืนค่ารายการที่ตรงกันทั้งหมด พร้อมข้อมูล
    ชื่อ-นามสกุล/คณะ/สาขา ให้ผู้ดูแลเห็นก่อนตัดสินใจกดลบจริง ป้องกันการลบผิดคน"""
    if shared_state.db is None:
        return False, [], "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"

    valid, target_text, err = _validate_member_query(query_text)
    if not valid:
        return False, [], err

    try:
        col_ref = shared_state.db.collection(Config.COLLECTION_MEMBER)
        matches = []

        def _to_row(doc_id, d):
            return {
                "member_id": doc_id,
                "prefix": d.get("prefix", ""),
                "first_name": d.get("first_name", ""),
                "last_name": d.get("last_name", ""),
                "faculty": d.get("faculty", "ไม่ระบุ"),
                "branch": d.get("branch", "ไม่ระบุ"),
            }

        # 1. ค้นหาด้วย Document ID ก่อน
        doc = col_ref.document(target_text).get()
        if doc.exists:
            matches.append(_to_row(doc.id, doc.to_dict() or {}))
        else:
            # 2. ค้นหาจากชื่อ หรือ ชื่อ-นามสกุล
            parts = target_text.split()
            if len(parts) >= 2:
                query_docs = col_ref.where(filter=FieldFilter("first_name", "==", parts[0]))\
                                     .where(filter=FieldFilter("last_name", "==", " ".join(parts[1:]))).stream()
            else:
                query_docs = col_ref.where(filter=FieldFilter("first_name", "==", target_text)).stream()

            for d_doc in query_docs:
                matches.append(_to_row(d_doc.id, d_doc.to_dict() or {}))

        if not matches:
            return False, [], f"ไม่พบข้อมูล '{target_text}' ในระบบ"

        return True, matches, ""
    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการค้นหาสมาชิก: {e}")
        return False, [], str(e)


def delete_member_by_exact_id(member_id):
    """ลบสมาชิกด้วย document ID ที่ผ่านการยืนยันแน่ชัดแล้วจาก find_member_for_deletion
    เท่านั้น (ไม่ค้นหาด้วยชื่อซ้ำตอนลบจริง) เพื่อรับประกันว่าลบตรงคนที่พรีวิวให้ดูจริง ๆ
    ไม่ใช่ผลลัพธ์ใหม่ที่อาจเปลี่ยนไปถ้าข้อมูลถูกแก้ระหว่างค้นหากับกดยืนยันลบ"""
    if shared_state.db is None:
        return False, "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"
    if not member_id:
        return False, "ไม่พบรหัสสมาชิกที่จะลบ"
    try:
        doc_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(member_id)
        doc = doc_ref.get()
        if not doc.exists:
            return False, f"ไม่พบข้อมูลรหัส '{member_id}' ในระบบ (อาจถูกลบไปแล้วก่อนหน้านี้)"
        doc_ref.delete()
        notify_members_updated()
        log(f"- ลบข้อมูลสมาชิกรายบุคคลสำเร็จ: {member_id}")
        return True, f"ลบข้อมูลของรหัส {member_id} สำเร็จ"
    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการลบข้อมูลรายบุคคล: {e}")
        return False, str(e)


def delete_member_by_id_or_name(query_text):
    """ลบข้อมูลสมาชิกรายบุคคลจาก รหัสสมาชิก หรือ ชื่อ-นามสกุล (เก็บไว้เพื่อความเข้ากันได้
    ย้อนหลัง - ตัว UI ใหม่ใช้ find_member_for_deletion() + delete_member_by_exact_id()
    แทน เพื่อให้เห็นข้อมูลก่อนลบและลบตรงคนที่ยืนยันจริง)"""
    if shared_state.db is None:
        return False, 0, "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"

    valid, target_text, err = _validate_member_query(query_text)
    if not valid:
        return False, 0, err

    try:
        col_ref = shared_state.db.collection(Config.COLLECTION_MEMBER)
        docs_to_delete = []

        doc = col_ref.document(target_text).get()
        if doc.exists:
            docs_to_delete.append(doc)
        else:
            parts = target_text.split()
            if len(parts) >= 2:
                matched_docs = col_ref.where(filter=FieldFilter("first_name", "==", parts[0]))\
                                      .where(filter=FieldFilter("last_name", "==", " ".join(parts[1:]))).stream()
            else:
                matched_docs = col_ref.where(filter=FieldFilter("first_name", "==", target_text)).stream()

            for d in matched_docs:
                docs_to_delete.append(d)

        if not docs_to_delete:
            return False, 0, f"ไม่พบข้อมูล '{target_text}' ในระบบ"

        batch = shared_state.db.batch()
        count = 0
        for d in docs_to_delete:
            batch.delete(d.reference)
            count += 1

        batch.commit()
        notify_members_updated()
        log(f"- ลบข้อมูลสมาชิกรายบุคคลสำเร็จ {count} รายการ ('{target_text}')")
        return True, count, f"ลบข้อมูลสำเร็จจำนวน {count} รายการ"

    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการลบข้อมูลรายบุคคล: {e}")
        return False, 0, str(e)

def add_single_student(student_id, prefix, first_name, last_name, faculty, branch):
    """ฟังก์ชันเพิ่มข้อมูลนักศึกษารายบุคคลเข้าสู่ Firestore Collection 'student'"""
    if shared_state.db is None:
        return False, "ระบบยังไม่ได้เชื่อมต่อกับ Firebase"

    student_id = student_id.strip()
    prefix = prefix.strip()
    first_name = first_name.strip()
    last_name = last_name.strip()
    faculty = faculty.strip()
    branch = branch.strip()

    try:
        # 1. เช็คว่ามีรหัสนักศึกษานี้อยู่ในระบบแล้วหรือไม่
        doc_ref = shared_state.db.collection(Config.COLLECTION_MEMBER).document(student_id)
        if doc_ref.get().exists:
            return False, f"รหัสนักศึกษา '{student_id}' มีอยู่ในระบบแล้ว"

        # 2. สร้าง Email อัตโนมัติจากรหัสนักศึกษา
        email = f"{student_id}@student.sru.ac.th"

        # 3. กำหนดโครงสร้างข้อมูลตามเงื่อนไข (ออโต้ฟิลด์)
        student_data = {
            "member_id": student_id,
            "prefix": prefix,
            "first_name": first_name,
            "last_name": last_name,
            "faculty": faculty,
            "branch": branch,
            "email": email,                   # ออโต้: รหัสนักศึกษา@student.sru.ac.th
            Config.FIELD_NAME: "",            # ออโต้: key ปล่อยว่าง
            "current_otp": 0,                 # ออโต้: 0
            "otp_expiry": 0,                  # ออโต้: 0
            "loginStatus": False,             # ออโต้: FALSE
            "checkinoutStatus": False,        # ออโต้: FALSE
            "last_status": "Clock-OUT",
            "last_update_date": "",
            "last_update_time": ""
        }

        # 4. บันทึกลง Firestore
        doc_ref.set(student_data, merge=True)
        notify_members_updated()  # รีเฟรชแคชสมาชิกในระบบ
        log(f"- เพิ่มข้อมูลนักศึกษารายบุคคลสำเร็จ ID: {student_id}")
        return True, f"เพิ่มนักศึกษารหัส {student_id} เรียบร้อยแล้ว"

    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการเพิ่มนักศึกษารายบุคคล: {e}")
        return False, str(e)

def get_faculties_and_branches():
    """ดึงข้อมูลรายการคณะและสาขาที่มีอยู่ทั้งหมดใน Firestore มาจัดกลุ่ม"""
    if shared_state.db is None:
        return [], {}

    try:
        docs = shared_state.db.collection(Config.COLLECTION_MEMBER).stream()
        faculty_map = {}

        for doc in docs:
            data = doc.to_dict()
            faculty = data.get("faculty", "").strip() if data.get("faculty") else ""
            branch = data.get("branch", "").strip() if data.get("branch") else ""

            if faculty:
                if faculty not in faculty_map:
                    faculty_map[faculty] = set()
                if branch:
                    faculty_map[faculty].add(branch)

        faculties = sorted(list(faculty_map.keys()))
        faculty_branches = {f: sorted(list(b)) for f, b in faculty_map.items()}
        return faculties, faculty_branches

    except Exception as e:
        log(f"❌ เกิดข้อผิดพลาดในการดึงรายการคณะ/สาขา: {e}")
        return [], {}