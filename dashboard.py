# dashboard.py
import os
import json
import threading
import webbrowser
from datetime import datetime, timedelta
from tkinter import messagebox
from google.cloud.firestore_v1.base_query import FieldFilter
from google import genai

from config import Config
import shared_state
from logger import log

# IN-MEMORY & FILE CACHE FOR FIRESTORE READ QUOTA PROTECTION
_raw_events_cache = []
_last_fetch_timestamp = None

_members_cache = {}
_last_members_fetch_timestamp = None

CACHE_EXPIRATION_MINUTES = 1
# สมาชิกใช้ .stream() อ่านทั้ง collection ทุกครั้งที่รีเฟรช (ไม่ใช่ delta query แบบ
# attendance) ถ้าใช้ TTL 1 นาทีเดียวกันแล้วมี background thread เรียกถี่ๆ ตลอดเวลา
# จะกลายเป็นอ่านสมาชิกทั้งหมดซ้ำทุกนาที (3,000 คน = 3,000 reads/นาที) ชนโควต้าอ่าน
# ฟรีของ Firestore (50,000/วัน) ภายในไม่ถึง 20 นาที จึงต้องแยก TTL ให้ยาวกว่ามาก
# ปกติสมาชิกจะได้ข้อมูลใหม่ทันทีอยู่แล้วผ่าน notify_members_updated() (force_refresh)
# ตอน import/แก้ไข/เพิ่มสมาชิกในแอปนี้ ค่านี้เป็นแค่ safety-net เผื่อมีการเขียนข้อมูล
# จากทางอื่นที่ไม่ได้เรียก notify (เช่น แก้ตรงใน Firebase Console)
MEMBERS_CACHE_EXPIRATION_MINUTES = 360  # 6 ชั่วโมง
_dashboard_refresh_guard = threading.Lock()

# GEMINI AI PREDICTION STATE & CACHE
_ai_status = {
    "status": "waiting",
    "message": "กำลังรอระบบพร้อม...",
    "text": ""
}
_ai_running = False
_last_ai_fetch_time = None

def _normalize_event(event):
    """Convert legacy student_id events to the member_id schema."""
    normalized = dict(event)
    if not normalized.get("member_id"):
        normalized["member_id"] = normalized.get("student_id", "")
    normalized.pop("student_id", None)
    return normalized


def _get_cached_members(force_refresh=False):
    global _members_cache, _last_members_fetch_timestamp
    now_ts = datetime.now()
    members_cache_path = os.path.join(Config.CACHE_DIR, "members_cache.json")
    
    if not _members_cache and os.path.exists(members_cache_path):
        try:
            with open(members_cache_path, 'r', encoding='utf-8') as f:
                _members_cache = json.load(f)
        except Exception as e:
            log(f"[WARN] Read Members Cache File Error: {e}")
            _members_cache = {}

    needs_fetch = force_refresh or (_last_members_fetch_timestamp is None) or \
                  ((now_ts - _last_members_fetch_timestamp).total_seconds() > MEMBERS_CACHE_EXPIRATION_MINUTES * 60)
    
    if needs_fetch and shared_state.db is not None:
        try:
            member_collection = getattr(Config, "COLLECTION_MEMBER", "MEMBER")
            member_docs = shared_state.db.collection(member_collection).stream()
            new_members = {}
            for doc in member_docs:
                d = doc.to_dict()
                m_id = d.get("member_id") or doc.id
                if m_id:
                    new_members[m_id] = {
                        "member_id": m_id,
                        "faculty": d.get("faculty", "ไม่ระบุ"),
                        "branch": d.get("branch", "ไม่ระบุ")
                    }
            if new_members:
                _members_cache = new_members
                os.makedirs(Config.CACHE_DIR, exist_ok=True)
                with open(members_cache_path, 'w', encoding='utf-8') as f:
                    json.dump(_members_cache, f, ensure_ascii=False)
                _last_members_fetch_timestamp = now_ts
                log(f"- [Members Cache Updated] Fetched {len(_members_cache)} members from DB.")
        except Exception as ex_mem:
            log(f"[ERROR] Fetch COLLECTION_MEMBER Error: {ex_mem}")

    registered_members_dict = dict(_members_cache)

    if hasattr(shared_state, 'valid_keys') and shared_state.valid_keys:
        for k, v in shared_state.valid_keys.items():
            m_id = v.get("member_id") or v.get("doc_id", k)
            if m_id:
                if m_id not in registered_members_dict:
                    registered_members_dict[m_id] = {
                        "member_id": m_id,
                        "faculty": v.get("faculty", "ไม่ระบุ"),
                        "branch": v.get("branch", "ไม่ระบุ")
                    }
                else:
                    if v.get("faculty") and v.get("faculty") not in ["ไม่ระบุ", "คณะไม่ระบุ"]:
                        registered_members_dict[m_id]["faculty"] = v.get("faculty")
                    if v.get("branch") and v.get("branch") != "ไม่ระบุ":
                        registered_members_dict[m_id]["branch"] = v.get("branch")

    if not registered_members_dict and _raw_events_cache:
        for ev in _raw_events_cache:
            m_id = ev.get("member_id")
            if m_id and m_id not in registered_members_dict:
                registered_members_dict[m_id] = {
                    "member_id": m_id,
                    "faculty": ev.get("faculty", "ไม่ระบุ"),
                    "branch": ev.get("branch", "ไม่ระบุ")
                }

    return list(registered_members_dict.values())

def notify_members_updated():
    """เรียกเมื่อมีการเพิ่ม/แก้ไขสมาชิก (รวมบุคคลภายนอก) เพื่อให้แดชบอร์ดอัปเดตจำนวนสมาชิกทันที"""
    global _last_members_fetch_timestamp
    _last_members_fetch_timestamp = None  # บังคับดึงสมาชิกใหม่จาก DB ในรอบถัดไป

    def do_refresh():
        update_dashboard_data_file(force_refresh=True, skip_ai_trigger=True)

    t = threading.Thread(target=do_refresh, daemon=True)
    t.start()

def notify_new_data_available():
    """เรียกเมื่อมีข้อมูลใหม่ (สมาชิก / attendance) เพื่อเขียน dashboard_data.js ใหม่ให้หน้าเว็บอัปเดตทันที"""
    global _last_members_fetch_timestamp
    _last_members_fetch_timestamp = None

    def do_refresh():
        update_dashboard_data_file(force_refresh=True, skip_ai_trigger=True)

    t = threading.Thread(target=do_refresh, daemon=True)
    t.start()


def manual_refresh_dashboard_cache():
    """เรียกจากปุ่ม 'อัปเดตแคชตอนนี้' ใน Admin GUI - บังคับล้างแคชเก่าทิ้งทั้งหมด (Hard Reset)
    และดึงข้อมูลใหม่จาก Firestore ตั้งแต่ศูนย์ เพื่อล้างข้อมูลที่ผิดพลาดหรือล้าสมัย"""
    # ประกาศ global ให้ครบเพื่อเข้าถึงตัวแปรแคชใน Memory
    global _last_members_fetch_timestamp, _last_fetch_timestamp, _raw_events_cache, _members_cache

    if shared_state.db is None:
        return False, "ยังไม่ได้เชื่อมต่อ Firebase กรุณารอสักครู่แล้วลองใหม่"

    if not _dashboard_refresh_guard.acquire(blocking=False):
        return False, "กำลังรีเฟรชแคชอยู่แล้ว กรุณารอสักครู่แล้วลองใหม่"

    try:
        # 1. ล้างแคชใน Memory ทิ้งทั้งหมด
        _raw_events_cache = []
        _members_cache = {}
        _last_fetch_timestamp = None
        _last_members_fetch_timestamp = None 

        # 2. ลบไฟล์แคชเดิมทิ้งเพื่อบังคับให้ระบบเขียนใหม่
        logs_path = Config.LOGS_CACHE_FILE
        members_path = os.path.join(Config.CACHE_DIR, "members_cache.json")
        
        if os.path.exists(logs_path):
            os.remove(logs_path)
        if os.path.exists(members_path):
            os.remove(members_path)

        # 3. ดึงข้อมูลใหม่ตั้งแต่ศูนย์ (Fallback จะดึงย้อนหลัง 365 วันอัตโนมัติเมื่อ _raw_events_cache ว่างเปล่า)
        update_dashboard_data_file(force_refresh=True, skip_ai_trigger=True)
        
        after_logs = len(_raw_events_cache)
        after_members = len(_members_cache)
        
        summary = (
            "ล้างแคชเก่าและดึงข้อมูลใหม่ทั้งหมดสำเร็จ!\n\n"
            f"อัปเดต Attendance logs: {after_logs} รายการ\n"
            f"อัปเดตสมาชิก: {after_members} คน"
        )
        return True, summary
    except Exception as e:
        from logger import log
        log(f"❌ Manual Refresh Error: {e}")
        return False, f"รีเฟรชแคชไม่สำเร็จ: {e}"
    finally:
        _dashboard_refresh_guard.release()

def _trigger_ai_prediction_async(events_cache, registered_members=None):
    global _ai_running, _ai_status, _last_ai_fetch_time
    
    if _ai_running:
        return

    now_ts = datetime.now()
    if _last_ai_fetch_time and (now_ts - _last_ai_fetch_time).total_seconds() < 900:
        return

    def run_ai():
        global _ai_running, _ai_status, _last_ai_fetch_time
        _ai_running = True
        
        _ai_status = {
            "status": "processing",
            "message": "Gemini AI กำลังวิเคราะห์ข้อมูลเชิงลึกและคาดการณ์พฤติกรรม...",
            "text": ""
        }
        update_dashboard_data_file(skip_ai_trigger=True)

        try:
            valid_events = [
                ev for ev in events_cache 
                if isinstance(ev, dict) 
                and ev.get("member_id") 
                and ev.get("date") 
                and ev.get("action") == "Clock-IN" 
            ]

            if not valid_events:
                _ai_status = {
                    "status": "waiting", 
                    "message": "ข้อมูลไม่เพียงพอสำหรับการวิเคราะห์เชิงลึก", 
                    "text": ""
                }
                return

            total_logs = len(valid_events)
            unique_members = len(set(ev.get("member_id") for ev in valid_events))
            
            target_members = registered_members if registered_members is not None else _get_cached_members()
            total_registered_members = len(target_members)

            member_ratio_pct = (unique_members / total_registered_members * 100) if total_registered_members > 0 else 0.0

            faculty_summary = {}
            branch_summary = {}
            hourly_summary = {f"{h:02d}:00": 0 for h in range(24)}
            external_count = 0
            dates = set()

            events_by_user_date = {}
            for ev in events_cache:
                m_id = ev.get("member_id")
                d_date = ev.get("date")
                if not m_id or not d_date: continue
                key = (m_id, d_date)
                events_by_user_date.setdefault(key, []).append(ev)

            user_stats = {}
            for (m_id, d_date), events in events_by_user_date.items():
                events.sort(key=lambda x: str(x.get("time", "00:00:00")))
                first_name = next((e.get("first_name") for e in reversed(events) if e.get("first_name")), "ไม่ระบุ")
                last_name = next((e.get("last_name") for e in reversed(events) if e.get("last_name")), "")
                full_name = f"{first_name} {last_name}".strip()
                fac = next((e.get("faculty") for e in reversed(events) if e.get("faculty") and e.get("faculty") not in ["ไม่ระบุ", "คณะไม่ระบุ"]), "ไม่ระบุ")
                br = next((e.get("branch") for e in reversed(events) if e.get("branch") and e.get("branch") != "ไม่ระบุ"), "ไม่ระบุ")

                if m_id not in user_stats:
                    user_stats[m_id] = {
                        "member_id": m_id,
                        "name": full_name or m_id,
                        "faculty": fac,
                        "branch": br,
                        "clock_ins": 0,
                        "total_hours": 0.0
                    }

                in_time = None
                for ev in events:
                    if ev.get("action") == "Clock-IN":
                        user_stats[m_id]["clock_ins"] += 1
                        if in_time is None:
                            in_time = ev.get("time")
                    elif ev.get("action") == "Clock-OUT" and in_time is not None:
                        try:
                            t1 = datetime.strptime(in_time, "%H:%M:%S")
                            t2 = datetime.strptime(ev.get("time"), "%H:%M:%S")
                            dur = (t2 - t1).total_seconds() / 3600.0
                            if dur > 0:
                                user_stats[m_id]["total_hours"] += dur
                        except Exception:
                            pass
                        in_time = None

            top_users_hours = sorted(user_stats.values(), key=lambda x: x["total_hours"], reverse=True)[:10]
            top_users_visits = sorted(user_stats.values(), key=lambda x: x["clock_ins"], reverse=True)[:10]

            top_hours_json = json.dumps([{
                "name": u["name"], "faculty": u["faculty"], "branch": u["branch"],
                "total_hours": round(u["total_hours"], 2), "total_visits": u["clock_ins"]
            } for u in top_users_hours], ensure_ascii=False)

            top_visits_json = json.dumps([{
                "name": u["name"], "faculty": u["faculty"], "branch": u["branch"],
                "total_visits": u["clock_ins"], "total_hours": round(u["total_hours"], 2)
            } for u in top_users_visits], ensure_ascii=False)

            for ev in valid_events:
                fac = ev.get("faculty", "ไม่ระบุ")
                br = ev.get("branch", "ไม่ระบุ")
                d_date = ev.get("date")
                d_time = ev.get("time", "00:00:00")
                dates.add(d_date)
                
                hour_key = f"{str(d_time).split(':')[0]}:00"
                if hour_key in hourly_summary:
                    hourly_summary[hour_key] += 1

                if fac == "บุคคลภายนอก":
                    external_count += 1
                else:
                    faculty_summary[fac] = faculty_summary.get(fac, 0) + 1
                    branch_summary[br] = branch_summary.get(br, 0) + 1

            date_range_str = f"{min(dates)} ถึง {max(dates)}" if dates else "ไม่ระบุ"
            peak_hour = max(hourly_summary, key=hourly_summary.get) if hourly_summary else "N/A"

            faculty_hours_summary = {}
            branch_hours_summary = {}
            for u in user_stats.values():
                fac = u.get("faculty", "ไม่ระบุ")
                br = u.get("branch", "ไม่ระบุ")
                hrs = u.get("total_hours", 0.0)
                
                if fac != "ไม่ระบุ" and fac != "คณะไม่ระบุ":
                    faculty_hours_summary[fac] = faculty_hours_summary.get(fac, 0.0) + hrs
                if br != "ไม่ระบุ":
                    branch_hours_summary[br] = branch_hours_summary.get(br, 0.0) + hrs

            # ปรับให้เป็นทศนิยม 2 ตำแหน่งเพื่อให้อ่านง่าย
            faculty_hours_summary = {k: round(v, 2) for k, v in faculty_hours_summary.items()}
            branch_hours_summary = {k: round(v, 2) for k, v in branch_hours_summary.items()}

            prompt = f"""[SYSTEM PRESET]
Role: คุณคือ Senior Chief Data Scientist และ Operational Intelligence Expert ขององค์กร หน้าที่หลักคือแปลข้อมูลดิบเป็นรายงานวิเคราะห์เชิงบริหารที่กระชับ อ่านง่าย และนำไปตัดสินใจได้ทันที

[กฎเหล็กการจัดรูปแบบ (STRICT FORMATTING RULES)]
1. No Fluff: ห้ามมีบทนำ คำเกริ่นนำ ประโยคทักทาย หรือประโยคสรุปปิดท้าย ให้เริ่มที่เนื้อหาตามหัวข้อแรกทันที
2. No Emojis: ห้ามใช้อีโมจิ สัญลักษณ์ไอคอน หรือกราฟิกตกแต่งทุกชนิดโดยเด็ดขาด
3. No Markdown Bold: ห้ามใช้ ** (เครื่องหมายดอกจันคู่) ในข้อความใดๆ เด็ดขาด ให้ใช้การขึ้นบรรทัดใหม่และตัวอักษรธรรมดาเท่านั้น
4. Data-Driven Only: ทุกข้อสังเกตและข้อสรุปต้องมีตัวเลขสถิติจากข้อมูลที่กำหนดให้อ้างอิงสนับสนุนเสมอ ห้ามคาดเดาข้อมูลนอกเหนือจากที่ปรากฏ
5. Formatting: ขึ้นบรรทัดใหม่เสมอเมื่อเริ่มหัวข้อใหม่ โดยใช้รูปแบบนี้เท่านั้น:
   - หัวข้อหลัก: ขึ้นบรรทัดใหม่ แล้วพิมพ์ ### ตามด้วยตัวเลข (เช่น ### 1. ชื่อหัวข้อ)
   - หัวข้อย่อย: ขึ้นบรรทัดใหม่ แล้วพิมพ์ 1.1 ตามด้วยชื่อหัวข้อ (เช่น 1.1 ชื่อหัวข้อ) โดยห้ามมี ** ครอบ
   - รายละเอียด: ขึ้นบรรทัดใหม่แล้วใช้ - นำหน้า
   - ห้ามใช้เส้นแบ่ง (---) หรือเว้นบรรทัดว่างติดกันเกิน 1 บรรทัด
6. Actionable Scope: ตอบเฉพาะเรื่องสถิติการเข้าใช้บริการ การบริหารจัดการพื้นที่ (Facility Management) และข้อเสนอแนะเชิงปฏิบัติการเท่านั้น

[ข้อมูลสถิติที่ผ่านการประมวลผล (DATA INPUT)]
* ช่วงเวลาข้อมูล: {date_range_str}
* จำนวนสมาชิกทั้งหมดในระบบ: {total_registered_members:,} คน
* จำนวนผู้เข้าใช้งานจริง (Unique Users): {unique_members:,} คน
* สัดส่วนการเข้าถึงบริการ (Penetration Rate): {member_ratio_pct:.2f}% ({unique_members:,} คน จากทั้งหมด {total_registered_members:,} คน)
* ปริมาณทราฟฟิกรวม (Total Clock-IN Logs): {total_logs:,} ครั้ง
* สถิติจำแนกตามคณะ (Faculty Distribution): {json.dumps(faculty_summary, ensure_ascii=False)}
* สถิติชั่วโมงรวมตามคณะ (Faculty Total Hours): {json.dumps(faculty_hours_summary, ensure_ascii=False)}
* สถิติจำแนกตามสาขา (Branch Distribution): {json.dumps(branch_summary, ensure_ascii=False)}
* สถิติชั่วโมงรวมตามสาขา (Branch Total Hours): {json.dumps(branch_hours_summary, ensure_ascii=False)}
* สัดส่วนผู้ใช้ภายนอก (External Users): {external_count:,} ครั้ง (คิดเป็น {(external_count/total_logs*100 if total_logs else 0):.1f}% ของทราฟฟิกทั้งหมด)
* ความหนาแน่นรายชั่วโมง (Hourly Traffic): {json.dumps(hourly_summary, ensure_ascii=False)}
* ช่วงเวลาใช้งานสูงสุด (Peak Hour): {peak_hour} น.
* Top 10 ผู้ใช้ (กลุ่ม Heavy Users เรียงตามชั่วโมงรวมสูงสุด): {top_hours_json}
* Top 10 ผู้ใช้ (กลุ่ม Frequent Users เรียงตามความถี่สูงสุด): {top_visits_json}

[คำสั่งวิเคราะห์เชิงลึก (DEEP ANALYSIS DIRECTIVES)]
เพื่อให้การวิเคราะห์ละเอียดยิ่งขึ้น ให้ใช้ข้อมูลที่มีอยู่ในการ Cross-reference ดังนี้:
1. เปรียบเทียบ Top 10 Heavy Users (ชั่วโมงรวม) กับ Top 10 Frequent Users (จำนวนครั้ง) ว่ามีพฤติกรรมแตกต่างกันอย่างไร (เช่น กลุ่มหนึ่งอยู่ยาว แต่อีกกลุ่มมาแป๊บเดียวบ่อยๆ) และคนเหล่านี้กระจุกตัวอยู่ในคณะ/สาขาใด
2. วิเคราะห์ความสัมพันธ์ระหว่าง Peak Hour กับคณะ/สาขา ว่าช่วงเวลาที่คนแน่นที่สุด มีกลุ่มผู้ใช้งานใดเป็นกลุ่มหลัก
3. ประเมินความเสี่ยงจากกลุ่ม Heavy Users ที่มีชั่วโมงรวมสูงผิดปกติ (เช่น อาจบ่งชี้ถึงการจองพื้นที่ทิ้งไว้ หรือการใช้งานผิดวัตถุประสงค์)
4. เปรียบเทียบ Penetration Rate กับปริมาณทราฟฟิกรวม เพื่อประเมินประสิทธิภาพการใช้งานพื้นที่จริง

[โครงสร้างรายงานบังคับ (REQUIRED STRUCTURE)]
### 1. การวิเคราะห์พฤติกรรมและแนวโน้มความหนาแน่น (Capacity & Peak Forecasting)
1.1 อัตราการเข้าถึงบริการ (Penetration Rate)
- [วิเคราะห์]
- [ประเมิน]
1.2 ความหนาแน่นรายชั่วโมงและช่วงเวลาสูงสุด (Hourly Traffic & Peak Hour)
- [วิเคราะห์]
- [ผลกระทบ]
### 2. การจำแนกพฤติกรรมตามกลุ่มผู้ใช้ (User Segmentation & Demographics)
2.1 กลุ่มผู้ใช้งานหลัก (Core Users)
- [ระบุ]
- [วิเคราะห์]
2.2 กลุ่มผู้ใช้งานภายนอก (External Users)
- [ประเมิน]
- [วิเคราะห์]
### 3. การตรวจจับความผิดปกติและข้อสังเกตเชิงบริหาร (Anomaly Detection & Risk Insights)
3.1 การตรวจจับพฤติกรรมผิดปกติ (Outliers & Heavy Use)
- [ค้นหา]
3.2 ความเสี่ยงด้านการปฏิบัติการ (Operational Risk)
- [ระบุ]
### 4. ข้อเสนอแนะเชิงกลยุทธ์ระดับองค์กร (Strategic Actionable Recommendations)
4.1 การบริหารจัดการช่วง Peak Hour
- [เสนอ]
4.2 การจัดสรรทรัพยากรและดึงดูดผู้ใช้กลุ่มใหม่
- [เสนอ]
"""
            api_key_path = getattr(Config, "GEMINI_API_KEY", "")
            if not api_key_path or not os.path.exists(api_key_path):
                _ai_status = {
                    "status": "error",
                    "message": "ไม่พบไฟล์ GEMINI_API_KEY",
                    "text": ""
                }
                log(f"[ERROR] Gemini AI Error: ไม่พบไฟล์ {api_key_path}")
                return

            # อ่านเนื้อหาจากไฟล์
            with open(api_key_path, "r", encoding="utf-8") as f:
                api_key = f.read().strip()

            if not api_key:
                _ai_status = {
                    "status": "error",
                    "message": "ไฟล์ API key ว่างเปล่า",
                    "text": ""
                }
                log("[ERROR] Gemini AI Error: API key is empty.")
                return

            client = genai.Client(api_key=api_key)
            response_stream = client.models.generate_content_stream(
                model='gemini-2.5-flash',
                contents=prompt,
            )

            _ai_status["status"] = "processing"
            _ai_status["text"] = ""

            for chunk in response_stream:
                if chunk.text:
                    _ai_status["text"] += chunk.text
                    update_dashboard_data_file(skip_ai_trigger=True)

            _ai_status["status"] = "success"
            _ai_status["message"] = "วิเคราะห์สำเร็จ"
            _last_ai_fetch_time = datetime.now()
            log("- [Gemini AI] วิเคราะห์แนวโน้มการเข้าใช้งานสำเร็จ")

        except Exception as ex_new:
            err_str = str(ex_new)
            if "429" in err_str or "quota" in err_str.lower() or "resource_exhausted" in err_str.lower():
                _ai_status = {
                    "status": "quota_exceeded",
                    "message": "โควตา Gemini API เกินชั่วคราว (429 Quota Exceeded) ระบบจะลองใหม่อัตโนมัติในรอบถัดไป",
                    "text": ""
                }
                log("[ERROR] Gemini AI Error: 429 Quota Exceeded.")
            else:
                _ai_status = {
                    "status": "error",
                    "message": f"ข้อผิดพลาด Gemini API: {err_str}",
                    "text": ""
                }
                log(f"[ERROR] Gemini AI Error: {err_str}")
        finally:
            _ai_running = False
            update_dashboard_data_file(skip_ai_trigger=True)

    t = threading.Thread(target=run_ai, daemon=True)
    t.start()

def update_dashboard_data_file(new_event=None, force_refresh=False, skip_ai_trigger=False):
    global _raw_events_cache, _last_fetch_timestamp
    
    os.makedirs(Config.CACHE_DIR, exist_ok=True)
    cache_path = Config.LOGS_CACHE_FILE

    with shared_state.dashboard_data_lock:
        try:
            today_date = datetime.now().strftime("%Y-%m-%d")
            
            if not _raw_events_cache and os.path.exists(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    _raw_events_cache = [_normalize_event(event) for event in json.load(f)]

            if new_event is not None:
                _raw_events_cache.append(_normalize_event(new_event))
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(_raw_events_cache, f, ensure_ascii=False)
            
            elif shared_state.db is not None:
                now_ts = datetime.now()
                needs_db_fetch = force_refresh or (_last_fetch_timestamp is None) or \
                                 ((now_ts - _last_fetch_timestamp).total_seconds() > CACHE_EXPIRATION_MINUTES * 60)
                
                if needs_db_fetch:
                    try:
                        latest_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
                        if _raw_events_cache:
                            latest_date = max([ev.get("date", "") for ev in _raw_events_cache] + [latest_date])

                        logs_ref = shared_state.db.collection(Config.COLLECTION_ATTENDANCE)\
                            .where(filter=FieldFilter("date", ">=", latest_date))\
                            .stream()
                        
                        new_fetched = []
                        existing_doc_ids = {
                            f"{e.get('date', '')}_{str(e.get('time', '')).replace(':', '')}_{e.get('member_id', '')}"
                            for e in _raw_events_cache
                        }

                        for doc in logs_ref:
                            d = doc.to_dict()
                            m_id = d.get("member_id", "")
                            d_time = d.get("time", "00:00:00")
                            d_date = d.get("date", "")
                            
                            doc_hash = f"{d_date}_{d_time.replace(':', '')}_{m_id}"
                            if not m_id or doc_hash in existing_doc_ids:
                                continue

                            member_info = next((v for v in shared_state.valid_keys.values() if v.get("doc_id") == m_id or v.get("member_id") == m_id), {})
                            
                            new_fetched.append({
                                "member_id": m_id,
                                "first_name": d.get("first_name") or member_info.get("first_name", "ไม่ระบุ"),
                                "last_name": d.get("last_name") or member_info.get("last_name", ""),
                                "action": d.get("action", ""),
                                "date": d_date,
                                "time": d_time,
                                "faculty": d.get("faculty") or member_info.get("faculty", "ไม่ระบุ"),
                                "branch": d.get("branch") or member_info.get("branch", "ไม่ระบุ")
                            })
                        
                        if new_fetched:
                            _raw_events_cache.extend(new_fetched)
                            with open(cache_path, 'w', encoding='utf-8') as f:
                                json.dump(_raw_events_cache, f, ensure_ascii=False)
                            log(f"- [Cache Updated] Fetched {len(new_fetched)} new logs (Delta) and saved to file.")
                        elif force_refresh:
                            # ปกติจะเงียบถ้าไม่มีอะไรใหม่ (กันสแปม log ทุก ๆ รอบ periodic refresh)
                            # แต่ถ้าเป็นการกดรีเฟรชเอง (force_refresh) ให้ยืนยันผลเสมอ จะได้รู้ว่า
                            # ฟังก์ชันทำงานจริง ไม่ใช่แค่เงียบเพราะพัง
                            log("- [Cache Check] Refreshed - no new attendance logs found since last sync.")

                        _last_fetch_timestamp = now_ts
                    except Exception as fb_err:
                        log(f"[ERROR] Fetch Config Error: {fb_err}")

            raw_events = _raw_events_cache
            all_logs = []
            latest_clock_in = {}

            for ev in raw_events:
                action = ev.get("action", "")
                m_id = ev.get("member_id", "")
                d_date = ev.get("date", "")
                d_time = ev.get("time", "00:00:00")
                faculty = ev.get("faculty", "ไม่ระบุ")
                branch = ev.get("branch", "ไม่ระบุ")
                first_name = ev.get("first_name", "ไม่ระบุ")
                last_name = ev.get("last_name", "")

                if action == "Clock-IN":
                    all_logs.append({
                        "member_id": m_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "date": d_date,
                        "time": d_time,
                        "faculty": faculty,
                        "branch": branch
                    })
                    if d_date == today_date:
                        if m_id not in latest_clock_in or d_time > latest_clock_in.get(m_id, ""):
                            latest_clock_in[m_id] = d_time

            events_by_user_date = {}
            for ev in raw_events:
                key = (ev.get("member_id", ""), ev.get("date", ""))
                if not key[0] or not key[1]:
                    continue
                if key not in events_by_user_date:
                    events_by_user_date[key] = []
                events_by_user_date[key].append(ev)

            session_data = []
            for key, events in events_by_user_date.items():
                member_id, d_date = key
                events.sort(key=lambda x: x["time"])

                total_hours_today = 0.0
                in_time = None

                faculty = next((e["faculty"] for e in reversed(events) if e.get("faculty") and e["faculty"] not in ["ไม่ระบุ", "คณะไม่ระบุ"]), "ไม่ระบุ")
                branch = next((e["branch"] for e in reversed(events) if e.get("branch") and e["branch"] != "ไม่ระบุ"), "ไม่ระบุ")
                first_name = next((e["first_name"] for e in reversed(events) if e.get("first_name")), "ไม่ระบุ")
                last_name = next((e["last_name"] for e in reversed(events) if e.get("last_name")), "")

                for ev in events:
                    if ev["action"] == "Clock-IN":
                        if in_time is None:
                            in_time = ev["time"]
                    elif ev["action"] == "Clock-OUT" and in_time is not None:
                        try:
                            t1 = datetime.strptime(in_time, "%H:%M:%S")
                            t2 = datetime.strptime(ev["time"], "%H:%M:%S")
                            duration_hours = (t2 - t1).total_seconds() / 3600.0

                            if duration_hours > 0:
                                total_hours_today += duration_hours
                        except Exception:
                            pass
                        in_time = None

                if total_hours_today > 0:
                    session_data.append({
                        "member_id": member_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "date": d_date,
                        "faculty": faculty,
                        "branch": branch,
                        "total_hours": total_hours_today
                    })

            inside_list = []
            if hasattr(shared_state, 'valid_keys') and shared_state.valid_keys:
                for k, info in shared_state.valid_keys.items():
                    if info.get("last_status") == "Clock-IN" and info.get("last_update_date") == today_date:
                        member_id = info.get("member_id") or info.get("doc_id", "")
                        time_in = info.get("last_update_time", "")
                        if not time_in or time_in == "-":
                            time_in = latest_clock_in.get(member_id, "-")

                        inside_list.append({
                            "first_name": info.get("first_name", "ไม่ระบุ"),
                            "last_name": info.get("last_name", ""),
                            "faculty": info.get("faculty", "ไม่ระบุ"),
                            "branch": info.get("branch", "ไม่ระบุ"),
                            "time_in": time_in
                        })

            registered_members = _get_cached_members(force_refresh=force_refresh)

            if not skip_ai_trigger:
                _trigger_ai_prediction_async(_raw_events_cache, registered_members)

            js_content = (
                f"window.rawData = {json.dumps(all_logs)};\n"
                f"window.insideData = {json.dumps(inside_list)};\n"
                f"window.sessionData = {json.dumps(session_data)};\n"
                f"window.registeredMembers = {json.dumps(registered_members)};\n"
                f"window.aiPredictionData = {json.dumps(_ai_status, ensure_ascii=False)};\n"
                f"window.geminiApiKey = {json.dumps(getattr(Config, 'GEMINI_API_KEY', ''))};\n"
                f"window.lastUpdatedTime = '{datetime.now().strftime('%H:%M:%S')}';"
            )
            file_path = os.path.join(os.getcwd(), Config.DASHBOARD_DIR_JS)

            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as file:
                file.write(js_content)
        except Exception as e:
            log(f"[ERROR] ข้อผิดพลาดในการอัปเดตข้อมูลแดชบอร์ด : {e}")


def show_dashboard_graph():
    if shared_state.db is None and not os.path.exists(Config.LOGS_CACHE_FILE):
        messagebox.showerror("ข้อผิดพลาด", "รอสักครู่ ระบบกำลังเชื่อมต่อฐานข้อมูล")
        return

    try:
        log("- กำลังเตรียมแดชบอร์ดสถิติระดับพรีเมียม...")

        def refresh_dashboard_data():
            if not _dashboard_refresh_guard.acquire(blocking=False):
                return
            try:
                update_dashboard_data_file(force_refresh=True)
            finally:
                _dashboard_refresh_guard.release()

        refresh_thread = threading.Thread(target=refresh_dashboard_data, daemon=True)
        refresh_thread.start()

        html_content = r"""
        <!DOCTYPE html>
        <html lang="th" class="light">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>แดชบอร์ดสถิติการเข้าใช้งาน</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <script>
                tailwind.config = {
                    darkMode: 'class'
                }
            </script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.css">
            <script src="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.6.13/flatpickr.min.js"></script>
            <style>
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; 
                    background-color: #f8fafc; 
                    color: #0f172a; 
                    transition: background-color 0.3s, color 0.3s; 
                    scroll-behavior: smooth; 
                    -webkit-font-smoothing: antialiased;
                    -moz-osx-font-smoothing: grayscale;
                    text-rendering: optimizeLegibility;
                }
                .dark body { background-color: #0f172a; color: #f8fafc; }

                .glass-card { 
                    background: white; 
                    border-radius: 16px; 
                    box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.04); 
                    border: 1px solid #f1f5f9; 
                    transition: background-color 0.3s, border-color 0.3s, box-shadow 0.3s, transform 0.3s; 
                }
                .dark .glass-card {
                    background: #1e293b;
                    border-color: #334155;
                    box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.3);
                }

                .form-select, .form-input {
                    width: 100%; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px;
                    font-size: 14px; outline: none; transition: all 0.2s; background: #fff; color: #1e293b;
                }
                .dark .form-select, .dark .form-input {
                    background: #0f172a; color: #f8fafc; border-color: #334155;
                }
                .form-select:focus, .form-input:focus { border-color: #3b82f6; box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.1); }
                .form-select:disabled, .form-input:disabled { background-color: #f1f5f9; color: #94a3b8; cursor: not-allowed; }
                .dark .form-select:disabled, .dark .form-input:disabled { background-color: #1e293b; color: #64748b; }

                .table-container::-webkit-scrollbar { width: 6px; height: 6px; }
                .table-container::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 4px; }
                .dark .table-container::-webkit-scrollbar-thumb { background-color: #475569; }

                .dark .flatpickr-calendar { background: #1e293b; border-color: #334155; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
                .dark .flatpickr-day { color: #f8fafc; }
                .dark .flatpickr-day:hover { background: #334155; }
                .dark .flatpickr-day.selected { background: #3b82f6; }
                .dark .flatpickr-months .flatpickr-month, .dark .flatpickr-current-month .flatpickr-monthDropdown-months { color: #f8fafc; fill: #f8fafc; }
                .dark span.flatpickr-weekday { color: #94a3b8; }

                .fade-in { animation: fadeIn 0.4s ease-in-out; }
                @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

                .btn-gemini {
                    background: linear-gradient(135deg, #2563eb 0%, #7c3aed 50%, #db2777 100%);
                    background-size: 200% 200%;
                    animation: geminiGradient 6s ease infinite;
                    box-shadow: 0 4px 15px -3px rgba(124, 58, 237, 0.35);
                }
                .btn-gemini:hover {
                    box-shadow: 0 8px 25px -4px rgba(124, 58, 237, 0.5);
                    transform: translateY(-1px);
                }
                @keyframes geminiGradient {
                    0% { background-position: 0% 50%; }
                    50% { background-position: 100% 50%; }
                    100% { background-position: 0% 50%; }
                }

                @keyframes targetHighlight {
                    0% { border-color: #f1f5f9; box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.04); }
                    25% { border-color: #3b82f6; box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.4), 0 10px 25px -5px rgba(59, 130, 246, 0.25); }
                    75% { border-color: #3b82f6; box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.4), 0 10px 25px -5px rgba(59, 130, 246, 0.25); }
                    100% { border-color: #f1f5f9; box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.04); }
                }
                .active-target { animation: targetHighlight 2s ease-in-out; position: relative; z-index: 30 !important; }

                #aiPredictionContent {
                    line-height: 1.85 !important;
                    font-size: 0.95rem;
                    letter-spacing: 0.01em;
                    cursor: text;
                    user-select: text;
                    -webkit-user-select: text;
                }
                #aiPredictionContent h4 {
                    font-size: 1.05rem;
                    font-weight: 600;
                    color: #0f172a;
                    margin-top: 1.25rem;
                    margin-bottom: 0.5rem;
                    padding-bottom: 0.35rem;
                    border-bottom: 1px solid #e2e8f0;
                }
                .dark #aiPredictionContent h4 {
                    color: #f8fafc;
                    border-bottom-color: #334155;
                }
                #aiPredictionContent li {
                    margin-top: 0.35rem;
                    margin-bottom: 0.35rem;
                }
            </style>
        </head>
        <body class="p-4 md:p-8 text-slate-800 dark:text-slate-100">
            <div class="max-w-screen-2xl mx-auto fade-in">

                <!-- Header Section -->
                <div class="flex flex-col md:flex-row justify-between items-start md:items-center mb-6 gap-4">
                    <div>
                        <h1 class="text-3xl font-bold text-slate-900 dark:text-white tracking-tight">แดชบอร์ดสถิติการเข้าใช้งาน</h1>
                        <p class="text-sm text-slate-500 dark:text-slate-400 mt-2 flex items-center gap-2 font-medium">
                            <span class="relative flex h-3 w-3">
                              <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                              <span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                            </span>
                            อัปเดตข้อมูลอัตโนมัติตามเวลาจริง
                        </p>
                    </div>
                    <div class="flex items-center gap-3">
                        <button id="themeToggleBtn" onclick="toggleTheme()" class="px-4 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-700 shadow-sm flex items-center gap-2 font-medium text-sm transition-all">
                            <svg id="themeIconDark" class="w-4 h-4 hidden dark:block text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
                            <svg id="themeIconLight" class="w-4 h-4 block dark:hidden text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"></path></svg>
                            <span id="themeText">โหมดมืด</span>
                        </button>
                        <div class="bg-white dark:bg-slate-800 px-5 py-2 rounded-lg border border-slate-200 dark:border-slate-700 shadow-sm flex flex-col justify-center">
                            <p class="text-[10px] text-slate-400 dark:text-slate-400 font-bold uppercase tracking-wider">เวลาปัจจุบัน</p>
                            <p id="currentTime" class="text-lg font-bold text-slate-700 dark:text-slate-200 leading-none mt-1"></p>
                        </div>
                    </div>
                </div>

                <!-- Main Layout -->
                <div class="flex flex-col lg:flex-row gap-6">
                    
                    <!-- Left Sidebar -->
                    <div class="w-full lg:w-72 shrink-0 flex flex-col gap-6">
                        <div class="glass-card p-6 sticky top-6">
                            <div class="flex items-center gap-2 mb-6">
                                <svg class="w-5 h-5 text-slate-400 dark:text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"></path></svg>
                                <h2 class="text-base font-semibold text-slate-700 dark:text-slate-200">ตัวกรองข้อมูล</h2>
                            </div>
                            
                            <div class="flex flex-col gap-4">
                                <div>
                                    <label class="block text-xs font-bold text-slate-500 dark:text-slate-400 mb-2 uppercase tracking-wide">คณะ</label>
                                    <select id="facFilter" class="form-select"></select>
                                </div>
                                <div>
                                    <label class="block text-xs font-bold text-slate-500 dark:text-slate-400 mb-2 uppercase tracking-wide">สาขา</label>
                                    <select id="branchFilter" class="form-select"></select>
                                </div>
                                <div>
                                    <label class="block text-xs font-bold text-slate-500 dark:text-slate-400 mb-2 uppercase tracking-wide">ช่วงเวลาด่วน</label>
                                    <select id="presetDateFilter" class="form-select">
                                        <option value="all" selected>ข้อมูลทั้งหมด</option>
                                        <option value="7">7 วันย้อนหลัง</option>
                                        <option value="15">15 วันย้อนหลัง</option>
                                        <option value="30">30 วันย้อนหลัง</option>
                                        <option value="custom">กำหนดเอง</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs font-bold text-slate-500 dark:text-slate-400 mb-2 uppercase tracking-wide">วันที่เริ่ม</label>
                                    <input type="text" id="startDate" class="form-input">
                                </div>
                                <div>
                                    <label class="block text-xs font-bold text-slate-500 dark:text-slate-400 mb-2 uppercase tracking-wide">วันที่สิ้นสุด</label>
                                    <input type="text" id="endDate" class="form-input">
                                </div>
                            </div>

                            <!-- GEMINI AI BUTTON -->
                            <div class="mt-6 pt-5 border-t border-slate-100 dark:border-slate-800">
                                <button onclick="openAiModal()" class="btn-gemini w-full py-3.5 px-4 rounded-xl text-white font-semibold active:scale-95 transition-all duration-200 flex items-center justify-center gap-2 text-sm tracking-wide">
                                    <svg class="w-5 h-5 shrink-0" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                        <defs>
                                            <linearGradient id="geminiBtnSparkleGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                                                <stop offset="0%" stop-color="#ffffff" />
                                                <stop offset="100%" stop-color="#e0e7ff" />
                                            </linearGradient>
                                        </defs>
                                        <path d="M16 2C16 6.41828 19.5817 10 24 10C19.5817 10 16 13.5817 16 18C16 13.5817 12.4183 10 8 10C12.4183 10 16 6.41828 16 2Z" fill="url(#geminiBtnSparkleGrad)"/>
                                        <path d="M7 14C7 16.2091 8.79086 18 11 18C8.79086 18 7 19.7909 7 22C7 19.7909 5.20914 18 3 18C5.20914 18 7 16.2091 7 14Z" fill="url(#geminiBtnSparkleGrad)"/>
                                        <path d="M4 3C4 4.10457 4.89543 5 6 5C4.89543 5 4 5.89543 4 7C4 5.89543 3.10457 5 2 5C3.10457 5 4 4.10457 4 3Z" fill="url(#geminiBtnSparkleGrad)"/>
                                    </svg>
                                    <span>วิเคราะห์แนวโน้มด้วย Gemini</span>
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Right Main Content -->
                    <div class="flex-1 flex flex-col gap-6 w-full overflow-hidden p-1">
                        
                        <!-- KPI Cards -->
                        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4">

                            <!-- Card 1 -->
                            <div class="glass-card p-5 border-l-4 border-l-emerald-500 relative cursor-pointer hover:-translate-y-1 hover:shadow-lg transition-all duration-300" 
                                 onclick="scrollToAndHighlight('liveTableSection')">
                                <h3 class="text-slate-500 dark:text-slate-400 font-semibold text-xs uppercase tracking-wide mb-1">ผู้ใช้งานในพื้นที่ขณะนี้</h3>
                                <div class="text-3xl font-extrabold text-slate-800 dark:text-white mt-2"><span id="kpi-inside">0</span> <span class="text-xs font-normal text-slate-400 dark:text-slate-500">คน</span></div>
                            </div>

                            <!-- Card 2 -->
                            <div class="glass-card p-5 border-l-4 border-l-blue-500 cursor-pointer hover:-translate-y-1 hover:shadow-lg transition-all duration-300"
                                 onclick="scrollToAndHighlight('compareChartSection')">
                                <h3 class="text-slate-500 dark:text-slate-400 font-semibold text-xs uppercase tracking-wide mb-1">จำนวนผู้เข้าใช้บริการ</h3>
                                <div class="text-3xl font-extrabold text-slate-800 dark:text-white mt-2"><span id="kpi-unique-users">0</span> <span class="text-xs font-normal text-slate-400 dark:text-slate-500">คน</span></div>
                            </div>

                            <!-- Card 0: สมาชิกทั้งหมดในระบบ -->
                            <div class="glass-card p-5 border-l-4 border-l-purple-500 relative transition-all duration-300">
                                <h3 class="text-slate-500 dark:text-slate-400 font-semibold text-xs uppercase tracking-wide mb-1" id="kpi-total-members-label">สมาชิกทั้งหมดในระบบ</h3>
                                <div class="text-3xl font-extrabold text-purple-600 dark:text-purple-400 mt-2">
                                    <span id="kpi-total-members">0</span> 
                                    <span class="text-xs font-normal text-slate-400 dark:text-slate-500">คน</span>
                                </div>
                            </div>

                            <!-- Card 3 -->
                            <div class="glass-card p-5 border-l-4 border-l-indigo-500 cursor-pointer hover:-translate-y-1 hover:shadow-lg transition-all duration-300"
                                 onclick="scrollToAndHighlight('trendChartSection')">
                                <h3 class="text-slate-500 dark:text-slate-400 font-semibold text-xs uppercase tracking-wide mb-1">ความถี่การเข้าใช้รวม</h3>
                                <div class="text-3xl font-extrabold text-slate-800 dark:text-white mt-2"><span id="kpi-total">0</span> <span class="text-xs font-normal text-slate-400 dark:text-slate-500">ครั้ง</span></div>
                            </div>

                            <!-- Card 4 -->
                            <div class="glass-card p-5 border-l-4 border-l-amber-500 flex flex-col justify-center h-full cursor-pointer hover:-translate-y-1 hover:shadow-lg transition-all duration-300"
                                 onclick="scrollToAndHighlight('topRankingsSection')">
                                <div id="kpi-top-group-container">
                                    <h3 class="text-slate-500 dark:text-slate-400 font-semibold text-xs uppercase tracking-wide mb-1" id="kpi-top-group-title">กลุ่มใช้งานสูงสุด</h3>
                                    <div class="text-sm font-bold text-slate-800 dark:text-white mt-0.5 truncate" id="kpi-top-group">-</div>
                                    <hr class="my-2 border-slate-100 dark:border-slate-800">
                                </div>
                                <div>
                                    <h3 class="text-slate-500 dark:text-slate-400 font-semibold text-xs uppercase tracking-wide mb-1">ช่วงเวลาหนาแน่นที่สุด</h3>
                                    <div class="text-xs font-bold text-slate-800 dark:text-white mt-0.5" id="kpi-peak-hour">-</div>
                                </div>
                            </div>
                        </div>

                        <!-- Top 5 Ranking Section -->
                        <div class="glass-card p-6 w-full" id="topRankingsSection">
                            <div class="flex justify-between items-center mb-4">
                                <div>
                                    <h2 class="text-base font-bold text-slate-700 dark:text-slate-200 flex items-center gap-2" id="top5Title">
                                        5 อันดับสมาชิกที่เข้าใช้งานสูงสุด (ระดับมหาวิทยาลัย)
                                    </h2>
                                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-1" id="top5Subtitle">จัดอันดับตามช่วงเวลาที่เลือก</p>
                                </div>
                                <div class="flex items-center gap-2">
                                    <label class="text-xs font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wide">เรียงตาม:</label>
                                    <select id="top5SortBy" class="form-select py-1.5 text-xs w-auto">
                                        <option value="visits" selected>จำนวนครั้ง (เข้าใช้งาน)</option>
                                        <option value="hours">เวลารวม (ชั่วโมง)</option>
                                    </select>
                                </div>
                            </div>
                            <div class="table-container max-h-96 overflow-auto border border-slate-100 dark:border-slate-800 rounded-xl">
                                <table class="w-full text-left border-collapse min-w-max">
                                    <thead>
                                        <tr class="bg-slate-50 dark:bg-slate-800/80 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider border-b border-slate-200 dark:border-slate-700">
                                            <th class="py-3 px-4 font-bold sticky top-0 bg-slate-50 dark:bg-slate-800 shadow-sm w-36">อันดับ</th>
                                            <th class="py-3 px-4 font-bold sticky top-0 bg-slate-50 dark:bg-slate-800 shadow-sm">ชื่อ - นามสกุล</th>
                                            <th class="py-3 px-4 font-bold sticky top-0 bg-slate-50 dark:bg-slate-800 shadow-sm" id="top5GroupHeader">คณะ</th>
                                            <th class="py-3 px-4 font-bold sticky top-0 bg-slate-50 dark:bg-slate-800 shadow-sm text-center">เวลารวม</th>
                                            <th class="py-3 px-4 font-bold sticky top-0 bg-slate-50 dark:bg-slate-800 shadow-sm text-right">เข้าใช้งาน</th>
                                        </tr>
                                    </thead>
                                    <tbody id="top5Body" class="text-sm text-slate-600 dark:text-slate-300">
                                        <!-- JS Injected -->
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Charts Section 1 -->
                        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                            <div class="glass-card p-6 w-full" id="trendChartSection">
                                <div class="flex justify-between items-center mb-4">
                                    <div>
                                        <h2 class="text-base font-bold text-slate-700 dark:text-slate-200">แนวโน้มสถิติการเข้าใช้งาน</h2>
                                        <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">เปรียบเทียบความถี่การเข้าใช้ (ครั้ง) และจำนวนผู้ใช้งานจริง (คน) รายวัน</p>
                                    </div>
                                </div>
                                <div class="relative h-72 w-full">
                                    <canvas id="trendChart"></canvas>
                                </div>
                            </div>
                            
                            <div class="glass-card p-6 w-full" id="avgTimeChartSection">
                                <h2 class="text-base font-bold text-slate-700 dark:text-slate-200 mb-2" id="avgTimeTitle">เวลาเฉลี่ยในการเข้าใช้พื้นที่</h2>
                                <p class="text-xs text-slate-500 dark:text-slate-400 mb-4" id="avgTimeSubtitle">คำนวณจากระยะเวลาที่ใช้งานในแต่ละวัน</p>
                                <div class="relative h-72 w-full">
                                    <canvas id="avgTimeChart"></canvas>
                                    <div id="avgTimeEmptyState" class="hidden absolute inset-0 flex flex-col items-center justify-center text-center px-6">
                                        <p class="text-sm font-semibold text-slate-500 dark:text-slate-400 mb-1">ยังไม่มีข้อมูลเวลาเฉลี่ยในช่วงที่เลือก</p>
                                        <p class="text-xs text-slate-400 dark:text-slate-500 leading-relaxed max-w-xs">
                                            สาเหตุหลักมักมาจากการที่สมาชิกที่มีแต่ประวัติ Clock-IN แต่ไม่มีการแตะบัตร Clock-OUT
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Charts Section 2 -->
                        <div class="grid grid-cols-1 gap-6">
                            <div class="glass-card p-6 w-full" id="compareChartSection">
                                <h2 class="text-base font-bold text-slate-700 dark:text-slate-200 mb-2" id="compareTitle">เปรียบเทียบความถี่การเข้าใช้งาน (ครั้ง) และ จำนวนผู้ใช้งาน (คน)</h2>
                                <p class="text-xs text-slate-500 dark:text-slate-400 mb-4">แสดงผลเปรียบเทียบเพื่อดูความหนาแน่นของผู้ใช้งาน</p>
                                <div class="relative h-80 w-full">
                                    <canvas id="compareChart"></canvas>
                                </div>
                            </div>
                        </div>

                        <!-- Live User Table -->
                        <div class="glass-card overflow-hidden w-full" id="liveTableSection">
                            <div class="p-6 border-b border-slate-100 dark:border-slate-800 flex justify-between items-center bg-white dark:bg-slate-800">
                                <h2 class="text-base font-bold text-slate-700 dark:text-slate-200 flex items-center gap-2">
                                    <span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
                                    รายชื่อผู้ที่กำลังใช้งานอยู่ในปัจจุบัน
                                </h2>
                            </div>
                            <div class="table-container max-h-96 overflow-auto bg-slate-50/50 dark:bg-slate-900/50">
                                <table class="w-full text-left border-collapse min-w-max">
                                    <thead>
                                        <tr class="bg-white dark:bg-slate-800 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider border-b border-slate-200 dark:border-slate-700">
                                            <th class="py-4 px-6 font-bold sticky top-0 bg-white dark:bg-slate-800 shadow-sm whitespace-nowrap">ลำดับ</th>
                                            <th class="py-4 px-6 font-bold sticky top-0 bg-white dark:bg-slate-800 shadow-sm whitespace-nowrap">เวลาเข้าล่าสุด</th>
                                            <th class="py-4 px-6 font-bold sticky top-0 bg-white dark:bg-slate-800 shadow-sm whitespace-nowrap">ชื่อ - นามสกุล</th>
                                            <th class="py-4 px-6 font-bold sticky top-0 bg-white dark:bg-slate-800 shadow-sm whitespace-nowrap">คณะ</th>
                                            <th class="py-4 px-6 font-bold sticky top-0 bg-white dark:bg-slate-800 shadow-sm whitespace-nowrap">สาขา</th>
                                        </tr>
                                    </thead>
                                    <tbody id="liveTableBody" class="text-sm text-slate-600 dark:text-slate-300">
                                        <!-- Data injected via JS -->
                                    </tbody>
                                </table>
                            </div>
                        </div>

                    </div>
                </div>
            </div>

            <!-- GEMINI AI MODAL WINDOW -->
            <div id="aiModal" class="fixed inset-0 z-50 hidden bg-slate-900/60 backdrop-blur-sm flex items-center justify-center p-3 md:p-6 transition-opacity duration-300" onclick="handleModalBackdropClick(event)">
                <div class="glass-card w-full max-w-4xl h-[90vh] flex flex-col shadow-2xl overflow-hidden border border-slate-200 dark:border-slate-700">
                    
                    <!-- Modal Header -->
                    <div class="p-4 md:p-5 border-b border-slate-100 dark:border-slate-800 flex justify-between items-center bg-white dark:bg-slate-800 shrink-0">
                        <div class="flex items-center gap-3">
                            <div class="p-2.5 rounded-xl bg-slate-900 dark:bg-slate-800 border border-slate-700 flex items-center justify-center shrink-0">
                                <svg class="w-6 h-6 shrink-0" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <defs>
                                        <linearGradient id="modalSparkleGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                                            <stop offset="0%" stop-color="#c084fc" />
                                            <stop offset="50%" stop-color="#38bdf8" />
                                            <stop offset="100%" stop-color="#818cf8" />
                                        </linearGradient>
                                    </defs>
                                    <path d="M16 2C16 6.41828 19.5817 10 24 10C19.5817 10 16 13.5817 16 18C16 13.5817 12.4183 10 8 10C12.4183 10 16 6.41828 16 2Z" fill="url(#modalSparkleGrad)"/>
                                    <path d="M7 14C7 16.2091 8.79086 18 11 18C8.79086 18 7 19.7909 7 22C7 19.7909 5.20914 18 3 18C5.20914 18 7 16.2091 7 14Z" fill="url(#modalSparkleGrad)"/>
                                    <path d="M4 3C4 4.10457 4.89543 5 6 5C4.89543 5 4 5.89543 4 7C4 5.89543 3.10457 5 2 5C3.10457 5 4 4.10457 4 3Z" fill="url(#modalSparkleGrad)"/>
                                </svg>
                            </div>
                            <div>
                                <h3 class="text-base font-bold text-slate-800 dark:text-slate-100">รายงานการวิเคราะห์เชิงลึกและคาดการณ์พฤติกรรม (Gemini AI)</h3>
                                <p class="text-xs text-slate-500 dark:text-slate-400">สรุปผลระดับองค์กร พร้อมระบบถาม-ตอบเพิ่มเติมต่อเนื่อง</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-3">
                            <span id="aiStatusBadge" class="px-3 py-1 text-xs font-semibold rounded-full bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border border-slate-200 dark:border-slate-700 shrink-0">
                                กำลังรอระบบพร้อม
                            </span>
                            <button onclick="closeAiModal()" class="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                            </button>
                        </div>
                    </div>

                    <!-- Modal Content Container -->
                    <div id="aiModalScrollBody" class="p-4 md:p-6 overflow-y-auto flex-1 bg-slate-50/50 dark:bg-slate-900/50 flex flex-col gap-4">
                        <div id="aiPredictionContent" contenteditable="true" class="text-sm text-slate-700 dark:text-slate-200 leading-relaxed bg-white dark:bg-slate-800 p-5 rounded-xl border border-slate-200 dark:border-slate-700/80 shadow-sm outline-none focus:ring-2 focus:ring-blue-500/50 transition-all">
                            ระบบกำลังเตรียมความพร้อมของข้อมูล AI...
                        </div>
                        <div id="aiChatThread" class="flex flex-col gap-3"></div>
                    </div>

                    <!-- Modal Footer & Input Bar -->
                    <div class="p-3 md:p-4 border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-800 shrink-0">
                        <div class="flex gap-2 items-center">
                            <input type="text" id="aiQuestionInput" 
                                   placeholder="ถามคำถามเพิ่มเติมเกี่ยวกับผลวิเคราะห์นี้..." 
                                   onkeydown="if(event.key === 'Enter') sendFollowUpQuestion()"
                                   class="form-input flex-1 py-2.5 text-sm bg-slate-50 dark:bg-slate-900 border-slate-200 dark:border-slate-700 focus:bg-white dark:focus:bg-slate-900">
                            <button id="sendAiQuestionBtn" onclick="sendFollowUpQuestion()" 
                                    class="px-5 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium text-sm transition-colors shadow-sm shrink-0 flex items-center gap-1.5">
                                <span>ส่งคำถาม</span>
                            </button>
                        </div>
                    </div>

                </div>
            </div>

            <!-- Scroll to Top Button -->
            <button id="scrollToTopBtn" onclick="scrollToTop()" class="fixed bottom-6 right-6 hidden z-50 p-3.5 bg-blue-600 hover:bg-blue-700 text-white rounded-full shadow-lg transition-all duration-300 transform hover:scale-110 focus:outline-none" title="กลับขึ้นด้านบน">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 15l7-7 7 7"></path>
                </svg>
            </button>

            <script>
                // Register DataLabels Plugin
                if (typeof ChartDataLabels !== 'undefined') {
                    Chart.register(ChartDataLabels);
                }

                // --- Scroll to Top Logic ---
                const scrollToTopBtn = document.getElementById('scrollToTopBtn');

                window.addEventListener('scroll', () => {
                    if (window.scrollY > 300) {
                        scrollToTopBtn.classList.remove('hidden');
                    } else {
                        scrollToTopBtn.classList.add('hidden');
                    }
                });

                function scrollToTop() {
                    window.scrollTo({
                        top: 0,
                        behavior: 'smooth'
                    });
                }

                // --- Dark / Light Mode Logic ---
                function initTheme() {
                    const savedTheme = localStorage.getItem('theme');
                    const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                    if (savedTheme === 'dark' || (!savedTheme && systemDark)) {
                        document.documentElement.classList.add('dark');
                        updateThemeUI(true);
                    } else {
                        document.documentElement.classList.remove('dark');
                        updateThemeUI(false);
                    }
                }

                function toggleTheme() {
                    const isDark = document.documentElement.classList.toggle('dark');
                    localStorage.setItem('theme', isDark ? 'dark' : 'light');
                    updateThemeUI(isDark);
                    if (typeof renderDashboard === 'function') renderDashboard();
                }

                function updateThemeUI(isDark) {
                    const text = document.getElementById('themeText');
                    if (text) text.innerText = isDark ? 'โหมดสว่าง' : 'โหมดมืด';
                }

                initTheme();

                function scrollToAndHighlight(targetId) {
                    const targetEl = document.getElementById(targetId);
                    if (!targetEl) return;

                    targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });

                    targetEl.classList.remove('active-target');
                    void targetEl.offsetWidth;
                    targetEl.classList.add('active-target');

                    setTimeout(() => {
                        targetEl.classList.remove('active-target');
                    }, 2000);
                }

                // --- AI Modal Control Functions ---
                function openAiModal() {
                    renderAIPredictionBlock();
                    const modal = document.getElementById('aiModal');
                    if (modal) modal.classList.remove('hidden');
                }

                function closeAiModal() {
                    const modal = document.getElementById('aiModal');
                    if (modal) modal.classList.add('hidden');
                }

                function handleModalBackdropClick(event) {
                    if (event.target.id === 'aiModal') {
                        closeAiModal();
                    }
                }

                document.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape') closeAiModal();
                });

                // --- Variables ---
                let lastDataString = "";
                let trendChartInstance = null;
                let avgTimeChartInstance = null;
                let compareChartInstance = null;
                let currentFilteredLogs = [];
                let aiChatHistory = [];
                window.isAiThinking = false;
                
                const categoricalColors = [
                    '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', 
                    '#ec4899', '#06b6d4', '#f97316', '#84cc16', '#64748b',
                    '#6366f1', '#14b8a6', '#eab308', '#d946ef', '#f43f5e'
                ];
                const thaiMonths = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];

                function truncateLabel(label, maxLength = 20) {
                    if (!label) return '';
                    return label.length > maxLength ? label.substring(0, maxLength) + '...' : label;
                }

                function formatDateLabel(dateStr) {
                    if (!dateStr) return '-';
                    const parts = dateStr.split('-');
                    if (parts.length !== 3) return dateStr;
                    const y = parseInt(parts[0], 10);
                    const m = parseInt(parts[1], 10) - 1;
                    const d = parseInt(parts[2], 10);
                    return `${d} ${thaiMonths[m]} ${String(y + 543).slice(-2)}`;
                }

                // --- UI Elements ---
                const facFilter = document.getElementById('facFilter');
                const branchFilter = document.getElementById('branchFilter');
                const presetDateFilter = document.getElementById('presetDateFilter');

                // --- Time Clock ---
                setInterval(() => {
                    const now = new Date();
                    const el = document.getElementById('currentTime');
                    if (el) el.innerText = now.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                }, 1000);

                // --- Flatpickr Setup ---
                const dateConfig = {
                    dateFormat: "Y-m-d",
                    altInput: true,
                    altFormat: "custom",
                    maxDate: "today",
                    formatDate: function (date, format) {
                        if (format === "custom") return date.getDate() + ' ' + thaiMonths[date.getMonth()] + ' ' + (date.getFullYear() + 543);
                        return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
                    },
                    onChange: () => { presetDateFilter.value = 'custom'; applyPresetDate(); renderDashboard(); }
                };
                const startPicker = flatpickr("#startDate", dateConfig);
                const endPicker = flatpickr("#endDate", dateConfig);

                // --- UI Status Management ---


                function formatAiText(text) {
                    if (!text) return '';
                    
                    // 1. ลบ Emoji
                    let cleaned = text.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F700}-\u{1F7FF}\u{1F800}-\u{1F8FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/gu, '');
                    
                    // 2. ลบ ** ทั้งหมดทิ้งไปเลย เพราะ AI ใช้ผิดวิธี
                    cleaned = cleaned.replace(/\*\*/g, '');

                    // 3. บังคับขึ้นบรรทัดใหม่ (ดักแก้กรณี AI ลืมขึ้นบรรทัดใหม่)
                    cleaned = cleaned.replace(/(###\s*\d+\.)/g, '\n\n$1');
                    // จับเฉพาะหัวข้อย่อย 1.1 ถึง 4.2 เท่านั้น (ไม่จับ 1.66)
                    cleaned = cleaned.replace(/([1-4]\.[1-2]\s+[ก-๙a-zA-Z])/g, '\n$1'); 

                    // 4. แปลงข้อความเป็น HTML ทีละบรรทัด (Line-by-Line Parser)
                    const lines = cleaned.split('\n');
                    let html = '';
                    let inList = false;

                    lines.forEach(line => {
                        const trimmed = line.trim();
                        if (!trimmed) return;

                        // ตรวจสอบหัวข้อหลัก (### 1. ...)
                        if (trimmed.startsWith('###')) {
                            if (inList) { html += '</ul>'; inList = false; }
                            html += `<h3 class="text-lg font-bold text-slate-900 dark:text-white mt-6 mb-3 border-b-2 border-slate-200 dark:border-slate-700 pb-1.5">${trimmed.replace(/^###\s*/, '')}</h3>`;
                        } 
                        // ตรวจสอบหัวข้อย่อย (1.1, 1.2, 2.1, 2.2, 3.1, 3.2, 4.1, 4.2)
                        else if (/^[1-4]\.[1-2]/.test(trimmed)) {
                            if (inList) { html += '</ul>'; inList = false; }
                            html += `<h4 class="font-semibold text-slate-800 dark:text-slate-100 mt-4 mb-2 text-base">${trimmed}</h4>`;
                        } 
                        // ตรวจสอบ Bullet points (- ...)
                        else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
                            if (!inList) { html += '<ul class="list-disc pl-5 my-2">'; inList = true; }
                            html += `<li class="text-slate-700 dark:text-slate-300 my-1 leading-relaxed">${trimmed.substring(2)}</li>`;
                        } 
                        // ข้อความบรรทัดปกติ
                        else {
                            if (inList) { html += '</ul>'; inList = false; }
                            html += `<p class="text-slate-700 dark:text-slate-300 my-2 leading-relaxed">${trimmed}</p>`;
                        }
                    });
                    
                    if (inList) html += '</ul>';
                    return html;
                }

                function renderAIPredictionBlock() {
                    const aiData = window.aiPredictionData || { status: 'waiting', message: 'กำลังรอระบบพร้อม...' };
                    const badge = document.getElementById('aiStatusBadge');
                    const content = document.getElementById('aiPredictionContent');

                    if (!badge || !content) return;
                    if (content.contains(document.activeElement) || document.activeElement === content) return;

                    if (aiData.status === 'processing') {
                        badge.className = "px-3 py-1 text-xs font-semibold rounded-full bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300 border border-blue-300 dark:border-blue-800 animate-pulse";
                        badge.innerText = "กำลังวิเคราะห์และเขียน...";
                        content.innerHTML = aiData.text 
                            ? `<div class="prose dark:prose-invert max-w-none text-sm leading-relaxed">${formatAiText(aiData.text)}</div>`
                            : `<div class="flex items-center gap-3 text-blue-600 dark:text-blue-400 font-medium py-4 animate-pulse">
                                <span>Gemini AI กำลังวิเคราะห์แนวโน้มและจัดทำรายงานเชิงลึก...</span>
                               </div>`;
                    } else if (aiData.status === 'success') {
                        badge.className = "px-3 py-1 text-xs font-semibold rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800";
                        badge.innerText = "วิเคราะห์สำเร็จ";
                        content.innerHTML = `<div class="prose dark:prose-invert max-w-none text-sm leading-relaxed">${formatAiText(aiData.text)}</div>`;
                    } else if (aiData.status === 'quota_exceeded') {
                        badge.className = "px-3 py-1 text-xs font-semibold rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300 border border-amber-300 dark:border-amber-800";
                        badge.innerText = "API Rate Limit (429)";
                        content.innerText = aiData.message;
                    } else if (aiData.status === 'error') {
                        badge.className = "px-3 py-1 text-xs font-semibold rounded-full bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border border-rose-300 dark:border-rose-800";
                        badge.innerText = "เกิดข้อผิดพลาด";
                        content.innerText = aiData.message;
                    } else {
                        badge.className = "px-3 py-1 text-xs font-semibold rounded-full bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border border-slate-300 dark:border-slate-700";
                        badge.innerText = "กำลังรอข้อมูล";
                        content.innerText = aiData.message || "กำลังรอข้อมูลจากระบบ...";
                    }
                }

                async function sendFollowUpQuestion() {
                    if (window.isAiThinking) return;

                    const inputEl = document.getElementById('aiQuestionInput');
                    const sendBtn = document.getElementById('sendAiQuestionBtn');
                    const question = inputEl.value.trim();
                    if (!question) return;

                    const apiKey = window.geminiApiKey || '';
                    if (!apiKey) {
                        alert('ไม่พบ GEMINI_API_KEY ในระบบ');
                        return;
                    }

                    window.isAiThinking = true;
                    inputEl.disabled = true;
                    sendBtn.disabled = true;
                    sendBtn.classList.add('opacity-50', 'cursor-not-allowed');
                    sendBtn.innerHTML = `<span>กำลังคิด...</span>`;

                    const chatThread = document.getElementById('aiChatThread');
                    const modalScrollBody = document.getElementById('aiModalScrollBody');
                    
                    const userMsgDiv = document.createElement('div');
                    userMsgDiv.className = "flex justify-end my-1";
                    userMsgDiv.innerHTML = `
                        <div class="bg-blue-600 text-white text-sm py-2.5 px-4 rounded-2xl max-w-[85%] shadow-sm">
                            <p class="font-bold text-[11px] text-blue-200 mb-0.5">คำถามเพิ่มเติม</p>
                            <div class="leading-relaxed">${question.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
                        </div>
                    `;
                    chatThread.appendChild(userMsgDiv);
                    inputEl.value = '';
                    modalScrollBody.scrollTop = modalScrollBody.scrollHeight;

                    const loadingDiv = document.createElement('div');
                    loadingDiv.className = "flex justify-start my-1";
                    loadingDiv.id = "aiLoadingMsg";
                    loadingDiv.innerHTML = `
                        <div class="bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300 text-sm py-2.5 px-4 rounded-2xl max-w-[85%] animate-pulse">
                            กำลังประมวลผลคำตอบ...
                        </div>
                    `;
                    chatThread.appendChild(loadingDiv);
                    modalScrollBody.scrollTop = modalScrollBody.scrollHeight;

                    if (aiChatHistory.length === 0 && window.aiPredictionData && window.aiPredictionData.text) {
                        aiChatHistory.push({
                            role: 'model',
                            parts: [{ text: window.aiPredictionData.text }]
                        });
                    }
                    
                    aiChatHistory.push({
                        role: 'user',
                        parts: [{ text: question }]
                    });

                    try {
                        const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                systemInstruction: {
                                    parts: [{ 
                                        text: "คุณคือ Senior Chief Data Scientist ผู้เชี่ยวชาญสถิติองค์กร\n" +
                                              "1. ตอบเฉพาะคำถามที่เกี่ยวข้องกับสถิติการเข้าใช้งาน ข้อมูลผู้ใช้งาน และรายงานแดชบอร์ดองค์กรเท่านั้น\n" +
                                              "2. ห้ามมีคำเกริ่นนำอารัมภบท ตอบตรงประเด็นทันที\n" +
                                              "3. ห้ามใช้อีโมจิใดๆ ทั้งสิ้น" 
                                    }]
                                },
                                contents: aiChatHistory
                            })
                        });

                        const data = await response.json();
                        const loadingMsg = document.getElementById('aiLoadingMsg');
                        if (loadingMsg) loadingMsg.remove();

                        if (data.candidates && data.candidates[0] && data.candidates[0].content) {
                            const replyText = data.candidates[0].content.parts[0].text;
                            
                            aiChatHistory.push({
                                role: 'model',
                                parts: [{ text: replyText }]
                            });

                            const modelMsgDiv = document.createElement('div');
                            modelMsgDiv.className = "flex justify-start my-1";
                            modelMsgDiv.innerHTML = `
                                <div class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-800 dark:text-slate-200 text-sm py-3 px-4 rounded-2xl max-w-[90%] shadow-sm">
                                    <p class="font-bold text-xs text-blue-600 dark:text-blue-400 mb-1 border-b border-slate-100 dark:border-slate-700/60 pb-1">คำตอบจาก Gemini AI</p>
                                    <div class="leading-relaxed">${formatAiText(replyText)}</div>
                                </div>
                            `;
                            chatThread.appendChild(modelMsgDiv);
                        } else {
                            throw new Error("ไม่สามารถรับคำตอบจาก AI ได้");
                        }
                    } catch (err) {
                        const loadingMsg = document.getElementById('aiLoadingMsg');
                        if (loadingMsg) loadingMsg.remove();

                        const errorDiv = document.createElement('div');
                        errorDiv.className = "flex justify-start my-1";
                        errorDiv.innerHTML = `
                            <div class="bg-rose-50 dark:bg-rose-950/60 text-rose-600 dark:text-rose-300 text-xs py-2 px-3 rounded-xl border border-rose-200 dark:border-rose-800">
                                เกิดข้อผิดพลาด: ${err.message}
                            </div>
                        `;
                        chatThread.appendChild(errorDiv);
                    } finally {
                        window.isAiThinking = false;
                        inputEl.disabled = false;
                        sendBtn.disabled = false;
                        sendBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                        sendBtn.innerHTML = `<span>ส่งคำถาม</span>`;
                        inputEl.focus();
                    }
                    modalScrollBody.scrollTop = modalScrollBody.scrollHeight;
                }

                function fetchLatestData(isInitial = false) {
                    let script = document.createElement('script');
                    script.src = 'dashboard_data.js?t=' + new Date().getTime();
                    script.onload = function() {
                        const rawData = window.rawData || [];
                        const insideData = window.insideData || [];
                        const sessionData = window.sessionData || [];
                        const registeredMembers = window.registeredMembers || [];
                        const aiData = window.aiPredictionData || {};
                        
                        const newDataString = JSON.stringify(rawData) + JSON.stringify(insideData) + JSON.stringify(sessionData) + JSON.stringify(registeredMembers) + JSON.stringify(aiData);

                        if (newDataString !== lastDataString) {
                            lastDataString = newDataString;
                            updateFilterDropdowns(isInitial);
                            if (isInitial) applyPresetDate();
                            renderDashboard();
                        }

                        renderAIPredictionBlock();
                        if (document.body.contains(script)) document.body.removeChild(script);
                    };
                    script.onerror = () => {
                        if (document.body.contains(script)) document.body.removeChild(script);
                    };
                    document.body.appendChild(script);
                }

                function updateFilterDropdowns(isInitial) {
                    const rawData = window.rawData || [];
                    const registeredMembers = window.registeredMembers || [];
                    const selFac = facFilter.value || "คณะทั้งหมด";

                    const allFaculties = [...new Set([...rawData.map(d => d.faculty), ...registeredMembers.map(m => m.faculty)])];

                    const normalFaculties = allFaculties
                        .filter(f => f && f !== "คณะทั้งหมด" && f !== "บุคคลภายนอก" && f !== "ไม่ระบุ" && f !== "คณะไม่ระบุ")
                        .sort();

                    let html = `<option value="คณะทั้งหมด">คณะทั้งหมด</option>`;
                    normalFaculties.forEach(f => {
                        html += `<option value="${f}">${f}</option>`;
                    });

                    html += `<option disabled class="text-slate-300 dark:text-slate-600">──────────</option>`;
                    html += `<option value="บุคคลภายนอก" class="font-semibold">บุคคลภายนอก</option>`;

                    facFilter.innerHTML = html;

                    if ([...normalFaculties, "คณะทั้งหมด", "บุคคลภายนอก"].includes(selFac)) {
                        facFilter.value = selFac;
                    }

                    updateBranchList();
                }

                function updateBranchList() {
                    const rawData = window.rawData || [];
                    const registeredMembers = window.registeredMembers || [];
                    const selFac = facFilter.value;
                    const prevBranch = branchFilter.value;

                    if (selFac === "บุคคลภายนอก") {
                        branchFilter.innerHTML = `<option value="สาขาทั้งหมด">-</option>`;
                        branchFilter.value = "สาขาทั้งหมด";
                        branchFilter.disabled = true;
                        return;
                    }

                    branchFilter.disabled = false;

                    let branches = ["สาขาทั้งหมด"];
                    if (selFac !== "คณะทั้งหมด") {
                        const filteredLogs = rawData.filter(d => d.faculty === selFac);
                        const filteredMembers = registeredMembers.filter(m => m.faculty === selFac);
                        const allBranches = [...new Set([...filteredLogs.map(d => d.branch), ...filteredMembers.map(m => m.branch)])];
                        branches = [...branches, ...allBranches.filter(b => b && b !== "ไม่ระบุ").sort()];
                    }

                    branchFilter.innerHTML = branches.map(b => `<option value="${b}">${b}</option>`).join('');
                    if (branches.includes(prevBranch)) branchFilter.value = prevBranch;
                }

                function applyPresetDate() {
                    const rawData = window.rawData || [];
                    const preset = presetDateFilter.value;
                    const today = new Date();
                    const todayStr = today.toISOString().split('T')[0];

                    if (preset === 'custom') {
                        startPicker.altInput.disabled = false; endPicker.altInput.disabled = false;
                        return;
                    }

                    startPicker.altInput.disabled = true; endPicker.altInput.disabled = true;

                    if (preset === 'all') {
                        const firstDateStr = rawData.length > 0 ? rawData.reduce((min, p) => p.date < min ? p.date : min, rawData[0].date) : todayStr;
                        startPicker.setDate(firstDateStr);
                    } else {
                        const pastDate = new Date();
                        pastDate.setDate(today.getDate() - parseInt(preset) + 1);
                        startPicker.setDate(pastDate.toISOString().split('T')[0]);
                    }
                    endPicker.setDate(todayStr);
                }

                function calculatePeakHour(logs) {
                    if (logs.length === 0) return "-";
                    let hourCounts = {};
                    logs.forEach(log => {
                        let hour = log.time.split(':')[0];
                        hourCounts[hour] = (hourCounts[hour] || 0) + 1;
                    });

                    let peakHour = Object.keys(hourCounts).reduce((a, b) => hourCounts[a] > hourCounts[b] ? a : b);
                    return `${peakHour}:00 - ${String(parseInt(peakHour)+1).padStart(2, '0')}:00 น.`;
                }

                function calculateTopStudents(logs, sessions = [], sortBy = 'visits') {
                    const memberMap = {};
                    logs.forEach(log => {
                        const memberId = log.member_id;
                        if (!memberMap[memberId]) {
                            memberMap[memberId] = {
                                member_id: memberId,
                                full_name: (log.first_name !== "ไม่ระบุ" ? log.first_name : "") + " " + (log.last_name || ""),
                                faculty: log.faculty || 'ไม่ระบุ',
                                branch: log.branch || 'ไม่ระบุ',
                                count: 0,
                                total_hours: 0.0
                            };
                            if (memberMap[memberId].full_name.trim() === "") {
                                memberMap[memberId].full_name = memberId;
                            }
                        }
                        memberMap[memberId].count += 1;
                    });

                    sessions.forEach(sess => {
                        if (sess.member_id && memberMap[sess.member_id]) {
                            memberMap[sess.member_id].total_hours += parseFloat(sess.total_hours || sess.duration || 0);
                        }
                    });

                    return Object.values(memberMap)
                        .sort((a, b) => {
                            return sortBy === 'hours' ? (b.total_hours - a.total_hours) : (b.count - a.count);
                        })
                        .slice(0, 5);
                }

                // --- Render Top 5 Badges ---
                function renderTop5Table(tbodyId, studentList, isFacultyView = false) {
                    const tbody = document.getElementById(tbodyId);
                    if (!studentList || studentList.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-400 dark:text-slate-500 bg-white dark:bg-slate-900">ไม่มีข้อมูลการเข้าใช้งานในระบบ</td></tr>';
                        return;
                    }

                    tbody.innerHTML = studentList.map((st, idx) => {
                        let rankBadge = '';
                        if (idx === 0) {
                            rankBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-100 dark:bg-amber-950/80 text-amber-700 dark:text-amber-300 font-bold text-xs border border-amber-300 dark:border-amber-700 shadow-sm">
                                🥇 เหรียญทอง
                            </span>`;
                        } else if (idx === 1) {
                            rankBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200 font-bold text-xs border border-slate-300 dark:border-slate-600 shadow-sm">
                                🥈 เหรียญเงิน
                            </span>`;
                        } else if (idx === 2) {
                            rankBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-orange-100 dark:bg-orange-950/80 text-orange-800 dark:text-orange-300 font-bold text-xs border border-orange-300 dark:border-orange-800 shadow-sm">
                                🥉 เหรียญทองแดง
                            </span>`;
                        } else {
                            rankBadge = `<span class="inline-flex items-center justify-center px-2.5 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 font-bold text-xs border border-slate-200 dark:border-slate-700">
                                #${idx + 1}
                            </span>`;
                        }

                        const hoursText = st.total_hours > 0 ? `${st.total_hours.toFixed(1)} ชม.` : '-';

                        const subText = isFacultyView ? (st.branch || 'ไม่ระบุ') : (st.faculty || 'ไม่ระบุ');

                        return `
                            <tr class="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors bg-white dark:bg-slate-900">
                                <td class="py-3 px-4 font-medium whitespace-nowrap">${rankBadge}</td>
                                <td class="py-3 px-4 font-semibold text-slate-700 dark:text-slate-200 whitespace-nowrap">${st.full_name}</td>
                                <td class="py-3 px-4 text-xs text-slate-500 dark:text-slate-400 max-w-[140px] truncate" title="${subText}">${subText}</td>
                                <td class="py-3 px-4 text-center whitespace-nowrap"><span class="bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 px-2.5 py-1 rounded-md font-bold text-xs">${hoursText}</span></td>
                                <td class="py-3 px-4 text-right whitespace-nowrap"><span class="bg-blue-50 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 px-2.5 py-1 rounded-md font-bold text-xs">${st.count} ครั้ง</span></td>
                            </tr>
                        `;
                    }).join('');
                }

                function renderDashboard() {
                    const isDark = document.documentElement.classList.contains('dark');
                    const chartTextColor = isDark ? '#94a3b8' : '#64748b';
                    const chartGridColor = isDark ? 'rgba(255, 255, 255, 0.08)' : '#f1f5f9';

                    const rawData = window.rawData || [];
                    const insideData = window.insideData || [];
                    const sessionData = window.sessionData || [];
                    const registeredMembers = window.registeredMembers || [];
                    const selFac = facFilter.value;
                    const selBranch = branchFilter.value;

                    const filteredMembers = registeredMembers.filter(m => {
                        if (selFac !== "คณะทั้งหมด" && m.faculty !== selFac) return false;
                        if (selBranch !== "สาขาทั้งหมด" && m.branch !== selBranch) return false;
                        return true;
                    });
                    document.getElementById('kpi-total-members').innerText = filteredMembers.length.toLocaleString();

                    const memberLabelEl = document.getElementById('kpi-total-members-label');
                    if (selFac === "คณะทั้งหมด") {
                        memberLabelEl.innerText = "สมาชิกทั้งหมดในระบบ";
                    } else if (selFac === "บุคคลภายนอก") {
                        memberLabelEl.innerText = "บุคคลภายนอกในระบบ";
                    } else if (selBranch !== "สาขาทั้งหมด") {
                        memberLabelEl.innerText = `สมาชิกในสาขา (${selBranch})`;
                    } else {
                        memberLabelEl.innerText = `สมาชิกในคณะ (${selFac})`;
                    }

                    const startD = startPicker.selectedDates[0] ? new Date(startPicker.selectedDates[0].setHours(0,0,0,0)) : new Date(0);
                    const endD = endPicker.selectedDates[0] ? new Date(endPicker.selectedDates[0].setHours(23,59,59,999)) : new Date();

                    const dateFilteredLogs = rawData.filter(d => {
                        const dt = new Date(d.date);
                        return dt >= startD && dt <= endD;
                    });

                    currentFilteredLogs = dateFilteredLogs.filter(d => {
                        if (selFac !== "คณะทั้งหมด" && d.faculty !== selFac) return false;
                        if (selBranch !== "สาขาทั้งหมด" && d.branch !== selBranch) return false;
                        return true;
                    });

                    const filteredInside = insideData.filter(d => {
                        if (selFac !== "คณะทั้งหมด" && d.faculty !== selFac) return false;
                        if (selBranch !== "สาขาทั้งหมด" && d.branch !== selBranch) return false;
                        return true;
                    });

                    const uniqueUsersCount = new Set(currentFilteredLogs.map(log => log.member_id)).size;
                    document.getElementById('kpi-inside').innerText = filteredInside.length;
                    document.getElementById('kpi-unique-users').innerText = uniqueUsersCount;
                    document.getElementById('kpi-total').innerText = currentFilteredLogs.length;
                    document.getElementById('kpi-peak-hour').innerText = calculatePeakHour(currentFilteredLogs);

                    const groupCounts = {};
                    const groupBy = selFac === "คณะทั้งหมด" ? "faculty" : "branch";
                    currentFilteredLogs.forEach(d => groupCounts[d[groupBy]] = (groupCounts[d[groupBy]] || 0) + 1);
                    const sortedGroups = Object.entries(groupCounts).sort((a,b) => b[1] - a[1]);

                    const kpiTopGroupContainer = document.getElementById('kpi-top-group-container');
                    if (selFac === "บุคคลภายนอก") {
                        if (kpiTopGroupContainer) kpiTopGroupContainer.classList.add('hidden');
                    } else {
                        if (kpiTopGroupContainer) kpiTopGroupContainer.classList.remove('hidden');
                        document.getElementById('kpi-top-group-title').innerText = selFac === "คณะทั้งหมด" ? "คณะที่เข้าใช้งานมากที่สุด" : "สาขาที่เข้าใช้งานมากที่สุด";
                        document.getElementById('kpi-top-group').innerText = sortedGroups.length > 0 ? sortedGroups[0][0] : "ไม่มีข้อมูล";
                    }

                    const top5Title = document.getElementById('top5Title');
                    const top5Subtitle = document.getElementById('top5Subtitle');
                    const top5GroupHeader = document.getElementById('top5GroupHeader');
                    const top5SortBy = document.getElementById('top5SortBy') ? document.getElementById('top5SortBy').value : 'visits';

                    let logsForRanking = [];
                    let isFacultyView = false;
                    const trophyIconHtml = `<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-extrabold bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300 border border-amber-300 dark:border-amber-700 mr-1.5 tracking-wide">TOP 5</span>`;
                    if (selFac === "คณะทั้งหมด") {
                        isFacultyView = false;
                        logsForRanking = dateFilteredLogs; 
                        top5Title.innerHTML = `${trophyIconHtml} 5 อันดับสมาชิกที่เข้าใช้งานสูงสุด (ระดับมหาวิทยาลัย)`;
                        top5Subtitle.innerText = `จัดอันดับเรียงตาม ${top5SortBy === 'hours' ? 'เวลารวม (ชั่วโมง)' : 'จำนวนครั้งการเข้าใช้งาน'} (ทุกคณะ)`;
                        top5GroupHeader.innerText = `คณะ`;
                    } else if (selFac === "บุคคลภายนอก") {
                        isFacultyView = true;
                        logsForRanking = currentFilteredLogs; 
                        top5Title.innerHTML = `${trophyIconHtml} 5 อันดับสมาชิกที่เข้าใช้งานสูงสุด (บุคคลภายนอก)`;
                        top5Subtitle.innerText = `จัดอันดับเฉพาะกลุ่มบุคคลภายนอก เรียงตาม ${top5SortBy === 'hours' ? 'เวลารวม (ชั่วโมง)' : 'จำนวนครั้ง'}`;
                        top5GroupHeader.innerText = `สังกัด/กลุ่ม`;
                    } else {
                        isFacultyView = true;
                        logsForRanking = currentFilteredLogs; 
                        top5Title.innerHTML = `${trophyIconHtml} 5 อันดับสมาชิกที่เข้าใช้งานสูงสุด (<span class="text-blue-600 dark:text-blue-400">คณะ${selFac}</span>)`;
                        top5Subtitle.innerText = `จัดอันดับจำแนกเฉพาะระดับคณะ เรียงตาม ${top5SortBy === 'hours' ? 'เวลารวม (ชั่วโมง)' : 'จำนวนครั้ง'}`;
                        top5GroupHeader.innerText = `สาขา`;
                    }

                    const filteredSessions = sessionData.filter(d => {
                        const dt = new Date(d.date);
                        if (dt < startD || dt > endD) return false;
                        if (selFac !== "คณะทั้งหมด" && d.faculty !== selFac) return false;
                        if (selBranch !== "สาขาทั้งหมด" && d.branch !== selBranch) return false;
                        return true;
                    });

                    const top5Data = calculateTopStudents(logsForRanking, filteredSessions, top5SortBy);
                    renderTop5Table('top5Body', top5Data, isFacultyView);

                    const tbody = document.getElementById('liveTableBody');
                    if(filteredInside.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-slate-400 dark:text-slate-500 bg-white dark:bg-slate-900">ไม่มีผู้ใช้งานที่ตรงตามเงื่อนไขในขณะนี้</td></tr>';
                    } else {
                        const sortedInside = filteredInside.sort((a, b) => b.time_in.localeCompare(a.time_in));
                        const maxDisplay = 50;
                        const displayList = sortedInside.slice(0, maxDisplay);

                        tbody.innerHTML = displayList.map((user, i) => `
                            <tr class="border-b border-slate-100 dark:border-slate-800 hover:bg-emerald-50/50 dark:hover:bg-emerald-950/30 transition-colors bg-white dark:bg-slate-900">
                                <td class="py-4 px-6 text-slate-500 dark:text-slate-400 font-medium whitespace-nowrap">${i + 1}</td>
                                <td class="py-4 px-6 whitespace-nowrap"><span class="bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 py-1 px-2 rounded-md text-xs font-bold">${user.time_in}</span></td>
                                <td class="py-4 px-6 font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">${user.first_name} ${user.last_name}</td>
                                <td class="py-4 px-6 text-slate-600 dark:text-slate-300 max-w-[200px] truncate" title="${user.faculty}">${user.faculty}</td>
                                <td class="py-4 px-6 text-slate-500 dark:text-slate-400 text-xs max-w-[200px] truncate" title="${user.branch}">${user.branch}</td>
                            </tr>
                        `).join('');

                        if (filteredInside.length > maxDisplay) {
                            tbody.innerHTML += `
                                <tr>
                                    <td colspan="5" class="py-4 text-center text-xs text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/50">
                                        กำลังแสดง ${maxDisplay} คนล่าสุด จากทั้งหมด ${filteredInside.length} คน
                                    </td>
                                </tr>
                            `;
                        }
                    }

                    const trendSummary = {};
                    const dailyUsersMap = {};

                    currentFilteredLogs.forEach(row => {
                        if (!row.date) return;
                        const key = row.date;
                        trendSummary[key] = (trendSummary[key] || 0) + 1;

                        if (!dailyUsersMap[key]) {
                            dailyUsersMap[key] = new Set();
                        }
                        dailyUsersMap[key].add(row.member_id);
                    });

                    const sortedKeys = Object.keys(trendSummary).sort();
                    const trendLabels = sortedKeys.map(k => formatDateLabel(k));
                    const trendFreqValues = sortedKeys.map(k => trendSummary[k]);
                    const trendUserValues = sortedKeys.map(k => dailyUsersMap[k] ? dailyUsersMap[k].size : 0);

                    if (trendChartInstance) trendChartInstance.destroy();
                    const ctx1 = document.getElementById('trendChart').getContext('2d');

                    const gradFreq = ctx1.createLinearGradient(0, 0, 0, 280);
                    gradFreq.addColorStop(0, 'rgba(99, 102, 241, 0.35)');
                    gradFreq.addColorStop(1, 'rgba(99, 102, 241, 0.01)');

                    const gradUser = ctx1.createLinearGradient(0, 0, 0, 280);
                    gradUser.addColorStop(0, 'rgba(16, 185, 129, 0.35)');
                    gradUser.addColorStop(1, 'rgba(16, 185, 129, 0.01)');

                    const isLargeDataset = sortedKeys.length > 50;

                    trendChartInstance = new Chart(ctx1, {
                        type: 'line',
                        data: {
                            labels: trendLabels,
                            datasets: [
                                { 
                                    label: ' ความถี่ (ครั้ง)', 
                                    data: trendFreqValues, 
                                    borderColor: '#6366f1',
                                    backgroundColor: gradFreq,
                                    borderWidth: isLargeDataset ? 1.8 : 2.5,
                                    tension: 0.3,
                                    fill: true,
                                    pointRadius: isLargeDataset ? 0 : 3,
                                    pointHoverRadius: 6,
                                    pointBackgroundColor: '#6366f1'
                                },
                                { 
                                    label: ' ผู้ใช้งาน (คน)', 
                                    data: trendUserValues, 
                                    borderColor: '#10b981',
                                    backgroundColor: gradUser,
                                    borderWidth: isLargeDataset ? 1.8 : 2.5,
                                    tension: 0.3,
                                    fill: true,
                                    pointRadius: isLargeDataset ? 0 : 3,
                                    pointHoverRadius: 6,
                                    pointBackgroundColor: '#10b981'
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            animation: isLargeDataset ? false : { duration: 400 },
                            normalized: true,
                            spanGaps: true,
                            interaction: { mode: 'index', intersect: false },
                            plugins: { 
                                datalabels: { display: false },
                                legend: { 
                                    display: true, 
                                    position: 'top', 
                                    align: 'end',
                                    labels: { font: {size: 12, weight: '500'}, color: chartTextColor, usePointStyle: true, boxWidth: 8 } 
                                }, 
                                tooltip: { 
                                    backgroundColor: isDark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(15, 23, 42, 0.9)', 
                                    titleFont: {size: 13, weight: '600'}, 
                                    bodyFont: {size: 12},
                                    padding: 12,
                                    cornerRadius: 8
                                } 
                            },
                            scales: {
                                y: { 
                                    beginAtZero: true, 
                                    grid: { color: chartGridColor }, 
                                    ticks: { precision: 0, color: chartTextColor } 
                                },
                                x: { 
                                    grid: { display: false }, 
                                    ticks: { 
                                        font: {size: 11}, 
                                        color: chartTextColor,
                                        autoSkip: true, 
                                        autoSkipPadding: 25,
                                        maxTicksLimit: 8,
                                        maxRotation: 45,
                                        minRotation: 0
                                    } 
                                }
                            }
                        }
                    });

                    document.getElementById('compareTitle').innerText = selFac === "คณะทั้งหมด" 
                        ? "เปรียบเทียบความถี่การเข้าใช้งาน (ครั้ง) และ จำนวนผู้ใช้งาน (คน) จำแนกตามคณะ" 
                        : (selFac === "บุคคลภายนอก" ? "เปรียบเทียบความถี่การเข้าใช้งานและผู้ใช้งาน (บุคคลภายนอก)" : "เปรียบเทียบความถี่การเข้าใช้งาน (ครั้ง) และ จำนวนผู้ใช้งาน (คน) จำแนกตามสาขา");

                    const distLabels = sortedGroups.map(g => g[0]);
                    const distValues = sortedGroups.map(g => g[1]);

                    const uniqueUsersMap = {};
                    currentFilteredLogs.forEach(d => {
                        if (!uniqueUsersMap[d.member_id]) {
                            uniqueUsersMap[d.member_id] = d;
                        }
                    });

                    const uniqueGroupCounts = {};
                    Object.values(uniqueUsersMap).forEach(d => {
                        const key = d[groupBy];
                        uniqueGroupCounts[key] = (uniqueGroupCounts[key] || 0) + 1;
                    });

                    const distUniqueValues = distLabels.map(label => uniqueGroupCounts[label] || 0);

                    if (compareChartInstance) compareChartInstance.destroy();
                    const ctx3 = document.getElementById('compareChart').getContext('2d');

                    compareChartInstance = new Chart(ctx3, {
                        type: 'bar',
                        data: {
                            labels: distLabels,
                            datasets: [
                                {
                                    label: ' ความถี่เข้าใช้งาน (ครั้ง)',
                                    data: distValues,
                                    backgroundColor: 'rgba(59, 130, 246, 0.85)',
                                    hoverBackgroundColor: '#2563eb',
                                    borderRadius: 6,
                                    barPercentage: 0.7,
                                    categoryPercentage: 0.6
                                },
                                {
                                    label: ' จำนวนผู้ใช้งานจริง (คน)',
                                    data: distUniqueValues,
                                    backgroundColor: 'rgba(16, 185, 129, 0.85)',
                                    hoverBackgroundColor: '#059669',
                                    borderRadius: 6,
                                    barPercentage: 0.7,
                                    categoryPercentage: 0.6
                                }
                            ]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            layout: {
                                padding: { top: 25 }
                            },
                            plugins: {
                                datalabels: {
                                    anchor: 'end',
                                    align: 'end',
                                    offset: 2,
                                    color: chartTextColor,
                                    font: { weight: 'bold', size: 11 },
                                    formatter: function(value) {
                                        return value > 0 ? value : '';
                                    }
                                },
                                legend: {
                                    display: true,
                                    position: 'top',
                                    align: 'end',
                                    labels: { font: {size: 12, weight: '500'}, color: chartTextColor, usePointStyle: true, boxWidth: 8 }
                                },
                                tooltip: {
                                    backgroundColor: isDark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(15, 23, 42, 0.9)',
                                    titleFont: {size: 13, weight: '600'},
                                    bodyFont: {size: 12},
                                    padding: 12,
                                    cornerRadius: 8
                                }
                            },
                            scales: {
                                y: {
                                    beginAtZero: true,
                                    grid: { color: chartGridColor },
                                    ticks: { display: false }
                                },
                                x: {
                                    grid: { display: false },
                                    ticks: {
                                        font: {size: 11},
                                        color: chartTextColor,
                                        callback: function(val, index) {
                                            const originalText = this.getLabelForValue(val);
                                            return truncateLabel(originalText, 18);
                                        }
                                    }
                                }
                            }
                        }
                    });

                    const avgTimeTitle = document.getElementById('avgTimeTitle');
                    const avgTimeSubtitle = document.getElementById('avgTimeSubtitle');

                    if (selFac === "คณะทั้งหมด") {
                        avgTimeTitle.innerText = "เวลาเฉลี่ยในการเข้าใช้พื้นที่ (จำแนกตามคณะ)";
                        avgTimeSubtitle.innerText = "แสดงชั่วโมงเฉลี่ยต่อคนตามคณะ ในช่วงเวลาที่เลือก";
                    } else if (selFac === "บุคคลภายนอก") {
                        avgTimeTitle.innerText = "เวลาเฉลี่ยในการเข้าใช้พื้นที่ (บุคคลภายนอก)";
                        avgTimeSubtitle.innerText = "แสดงชั่วโมงเฉลี่ยของกลุ่มบุคคลภายนอก ในช่วงเวลาที่เลือก";
                    } else {
                        avgTimeTitle.innerText = `เวลาเฉลี่ยในการเข้าใช้พื้นที่ (คณะ${selFac})`;
                        avgTimeSubtitle.innerText = "แสดงชั่วโมงเฉลี่ยต่อคนตามสาขา ในช่วงเวลาที่เลือก";
                    }

                    const groupHours = {};
                    const groupUsers = {};

                    filteredSessions.forEach(d => {
                        const groupKey = selFac === "คณะทั้งหมด" ? d.faculty : d.branch;
                        if (!groupKey) return;

                        if (!groupHours[groupKey]) {
                            groupHours[groupKey] = 0.0;
                            groupUsers[groupKey] = new Set();
                        }
                        groupHours[groupKey] += d.total_hours;
                        groupUsers[groupKey].add(d.member_id);
                    });

                    const avgLabels = [];
                    const avgValues = [];

                    Object.keys(groupHours).forEach(gKey => {
                        const userCount = groupUsers[gKey] ? groupUsers[gKey].size : 0;
                        if (userCount > 0) {
                            const avgVal = groupHours[gKey] / userCount;
                            avgLabels.push(gKey);
                            avgValues.push(parseFloat(avgVal.toFixed(2)));
                        }
                    });

                    const avgEmptyState = document.getElementById('avgTimeEmptyState');
                    if (avgValues.length === 0) {
                        if (avgEmptyState) avgEmptyState.classList.remove('hidden');
                    } else {
                        if (avgEmptyState) avgEmptyState.classList.add('hidden');
                    }

                    // --- 1. แผนภูมิเวลาเฉลี่ยในการเข้าใช้พื้นที่ (ปรับเป็น Horizontal Bar เพื่อลบพื้นที่ว่างด้านหน้า) ---
                    if (avgTimeChartInstance) avgTimeChartInstance.destroy();
                    const ctx2 = document.getElementById('avgTimeChart').getContext('2d');

                    avgTimeChartInstance = new Chart(ctx2, {
                        type: 'bar',
                        data: {
                            labels: avgLabels,
                            datasets: [{
                                label: ' เวลาเฉลี่ย (ชั่วโมง/คน)',
                                data: avgValues,
                                backgroundColor: avgLabels.map((_, i) => categoricalColors[i % categoricalColors.length]),
                                borderRadius: 6,
                                barPercentage: 0.6
                            }]
                        },
                        options: {
                            indexAxis: 'y',
                            responsive: true,
                            maintainAspectRatio: false,
                            layout: {
                                padding: { right: 45, left: 0 }
                            },
                            plugins: {
                                datalabels: {
                                    anchor: 'end',
                                    align: 'end',
                                    offset: 4,
                                    color: chartTextColor,
                                    font: { weight: 'bold', size: 11 },
                                    formatter: function(value) {
                                        return value > 0 ? value + ' ชม.' : '';
                                    }
                                },
                                legend: { display: false },
                                tooltip: {
                                    backgroundColor: isDark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(15, 23, 42, 0.9)',
                                    titleFont: {size: 13, weight: '600'},
                                    bodyFont: {size: 12},
                                    padding: 12,
                                    cornerRadius: 8,
                                    callbacks: {
                                        label: function(context) {
                                            return ` เวลาเฉลี่ย: ${context.parsed.x} ชม./คน`;
                                        }
                                    }
                                }
                            },
                            scales: {
                                x: {
                                    beginAtZero: true,
                                    grid: { color: chartGridColor },
                                    ticks: { display: false }
                                },
                                y: {
                                    grid: { display: false },
                                    ticks: {
                                        font: {size: 11},
                                        color: chartTextColor,
                                        callback: function(val) {
                                            return truncateLabel(this.getLabelForValue(val), 18);
                                        }
                                    }
                                }
                            }
                        }
                    });
                }

                facFilter.addEventListener('change', () => { updateBranchList(); renderDashboard(); });
                branchFilter.addEventListener('change', renderDashboard);
                presetDateFilter.addEventListener('change', () => { applyPresetDate(); renderDashboard(); });
                const top5SortEl = document.getElementById('top5SortBy');
                if (top5SortEl) top5SortEl.addEventListener('change', renderDashboard);

                fetchLatestData(true);
                setInterval(() => { fetchLatestData(false); }, 5000);
            </script>
        </body>
        </html>
        """

        temp_html_path = os.path.join(os.getcwd(), Config.DASHBOARD_DIR_HTML)
        os.makedirs(os.path.dirname(temp_html_path), exist_ok=True)
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        webbrowser.open(f"file://{os.path.abspath(temp_html_path)}")

    except Exception as e:
        log(f"[ERROR] ไม่สามารถแสดงผลแดชบอร์ดได้ : {e}")
        messagebox.showerror("ข้อผิดพลาด", f"ไม่สามารถแสดงผลกราฟได้: {e}")