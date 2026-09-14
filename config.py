# config.py
class Config:
    RSSI_THRESHOLD = -66  # ระยะประมาณ 1 เมตร
    UNLOCK_DELAY = 3

    FB_KEY_PATH = "key/studentdata-37c33-firebase-adminsdk-fbsvc-cb5aa64e79.json"
    COLLECTION_STUDENT = "student"
    COLLECTION_ATTENDANCE = "attendance_logs"
    COLLECTION_CONFIG = "connect"
    FIELD_NAME = "key"

    LOG_FILE = "system_log/log.txt"
    DASHBOARD_DIR_JS = "dashboard/dashboard_data.js"
    DASHBOARD_DIR_HTML = "dashboard/dashboard.html"

    CACHE_DIR = "DB_Cache"
    USERS_CACHE_FILE = "DB_Cache/users_cache.json"
    LOGS_CACHE_FILE = "DB_Cache/logs_cache.json"
    