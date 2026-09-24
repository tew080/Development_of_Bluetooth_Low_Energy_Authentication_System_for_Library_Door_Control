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
