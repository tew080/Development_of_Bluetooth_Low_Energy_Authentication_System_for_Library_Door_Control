# db_manager.py
import csv
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
from dashboard import update_dashboard_data_file
from logger import log

import os
import json

def init_firebase():
    try:
        if not firebase_admin._apps:
            cred = fb_credentials.Certificate(Config.FB_KEY_PATH)
            firebase_admin.initialize_app(cred)
        shared_state.db = firestore.client()
        fetch_ble_config()
        shared_state.db.collection(Config.COLLECTION_MEMBER).on_snapshot(
            on_snapshot_update
        )
        log("- Firebase Connected & Syncing...")
    except Exception as e:
        log(f"❌ Firebase Init Error: {e}")
        exit(1)


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


def on_snapshot_update(col_snapshot, changes, read_time):
    """
    ดึงข้อมูลและอัปเดต In-Memory เฉพาะ Document ที่มีการเปลี่ยนแปลง (Added, Modified, Removed)
    """
    try:
        if getattr(shared_state, 'valid_keys', None) is None:
            shared_state.valid_keys = {}

        for change in changes:
            doc = change.document
            data = doc.to_dict()
            key = str(data.get(Config.FIELD_NAME, "")).strip()

            if change.type.name in ['ADDED', 'MODIFIED']:
                
                # 🔥 [จุดที่ต้องแก้] 1. บังคับลบคีย์เก่าทั้งหมดของ user คนนี้ออกไปก่อนเสมอ ป้องกันข้อมูลค้าง
                keys_to_delete = [k for k, v in shared_state.valid_keys.items() if v.get("doc_id") == doc.id]
                for k in keys_to_delete:
                    del shared_state.valid_keys[k]

                # 2. ถ้ามีคีย์ใหม่ (สถานะกำลังล็อกอิน) ถึงจะนำกลับเข้าไปใหม่
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
                    
            elif change.type.name == 'REMOVED':
                keys_to_delete = [k for k, v in shared_state.valid_keys.items() if v.get("doc_id") == doc.id]
                for k in keys_to_delete:
                    del shared_state.valid_keys[k]

        log(f"- Database Sync: Updated memory (Delta Sync). Total active keys: {len(shared_state.valid_keys)}")
        
    except Exception as e:
        log(f"❌ Firebase Update Error: {e}")

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

def init_firebase():
    try:
        load_local_users_cache() # ดึงจากไฟล์แคชก่อน
        if not firebase_admin._apps:
            cred = fb_credentials.Certificate(Config.FB_KEY_PATH)
            firebase_admin.initialize_app(cred)
        shared_state.db = firestore.client()
        fetch_ble_config()
        shared_state.db.collection(Config.COLLECTION_MEMBER).on_snapshot(on_snapshot_update)
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

        