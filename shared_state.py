# shared_state.py
import threading

valid_keys = {}
is_processing = False
db = None
gui_light_state = "red"
gui_user_name = ""
gui_action_text = ""
dashboard_data_lock = threading.Lock()
# เพิ่ม dict สำหรับเก็บเวลาที่สแกนล่าสุดของแต่ละ key
last_scanned_times = {}

# Offline queue สำหรับ attendance ที่ยังอัปขึ้น Firestore ไม่ได้
pending_attendance_lock = threading.Lock()
pending_attendance_queue = []  # list of dict (new_log_event + member update fields)
