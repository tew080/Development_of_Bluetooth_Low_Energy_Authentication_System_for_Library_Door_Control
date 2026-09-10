import threading
import tkinter as tk
from tkinter import ttk, messagebox

from google.cloud.firestore_v1.base_query import FieldFilter

import shared_state
from ble_scanner import run_background_scanner
from dashboard import show_dashboard_graph
from db_manager import (
    import_csv_to_firebase,
    import_attendance_csv_to_firebase,
    get_student_by_id,
    update_student_data,
    get_admin_email_config,
    update_admin_email_config,
    get_ble_connect_config,
    update_ble_connect_config,
    add_external_person,
)
from config import Config

def bind_fullscreen(window):
    """
    ฟังก์ชันสำหรับเปิดใช้งานโหมดเต็มจอ (Fullscreen) 
    โดยกดปุ่ม F11 เพื่อสลับโหมด และปุ่ม Escape เพื่อออกจากโหมดเต็มจอ
    """
    window.attributes("-fullscreen", False)
    
    def toggle_fullscreen(event=None):
        state = not window.attributes("-fullscreen")
        window.attributes("-fullscreen", state)
        
    def end_fullscreen(event=None):
        window.attributes("-fullscreen", False)

    window.bind("<F11>", toggle_fullscreen)
    window.bind("<Escape>", end_fullscreen)

def open_edit_window(parent):
    """
    หน้าต่างแก้ไขข้อมูลของระบบ (Collection Manager)
    แบ่งออกเป็น 3 แท็บ ได้แก่: ข้อมูลนักศึกษา, ตั้งค่าอีเมลผู้ดูแล, และตั้งค่าการเชื่อมต่อ BLE
    - มีการโหลดข้อมูลจาก Firebase แบบ Asynchronous (ทำงานเบื้องหลัง) เพื่อป้องกันหน้าต่างค้าง
    - สามารถขยายเต็มจอได้เพื่อความชัดเจนในการป้อนข้อมูล
    """
    edit_win = tk.Toplevel(parent)
    edit_win.title("แก้ไขข้อมูลระบบ (Collection Manager) - กด F11 เพื่อเต็มจอ")
    edit_win.geometry("700x600")
    edit_win.configure(bg="#2c3e50")
    
    # เปิดให้สามารถย่อขยายหน้าต่างได้อิสระ
    edit_win.resizable(True, True)
    bind_fullscreen(edit_win)

    edit_win.transient(parent)
    edit_win.grab_set()

    # ตั้งค่าสไตล์ของ Tab (Notebook)
    style = ttk.Style()
    style.theme_use('default')
    style.configure("TNotebook", background="#2c3e50", borderwidth=0)
    style.configure("TNotebook.Tab", font=("Arial", 12, "bold"), padding=[15, 8])

    # สร้าง Main Frame เพื่อให้อยู่กึ่งกลางหน้าจอเสมอเมื่อขยาย
    main_container = tk.Frame(edit_win, bg="#2c3e50")
    main_container.pack(expand=True, fill=tk.BOTH, padx=20, pady=20)

    notebook = ttk.Notebook(main_container)
    notebook.pack(fill=tk.BOTH, expand=True)

# ==========================================
    # TAB 1: จัดการข้อมูลนักศึกษา (student)
    # ==========================================
    tab_student = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_student, text="ข้อมูลนักศึกษา ")

    st_center_frame = tk.Frame(tab_student, bg="#34495e")
    st_center_frame.pack(expand=True, fill=tk.BOTH, pady=20)

    search_frame = tk.Frame(st_center_frame, bg="#34495e")
    search_frame.pack(pady=(10, 15))

    # ปรับข้อความ Label ให้ครอบคลุมการค้นหา
    tk.Label(search_frame, text="ค้นหารหัส / ชื่อ-สกุล:", bg="#34495e", font=("Arial", 13, "bold"), fg="white").grid(row=0, column=0, padx=5)
    entry_search = tk.Entry(search_frame, font=("Arial", 13), width=25)
    entry_search.grid(row=0, column=1, padx=5)

    tk.Label(search_frame, text="* กรณีค้นหาด้วยชื่อ ให้พิมพ์: ชื่อ เว้นวรรค นามสกุล", bg="#34495e", font=("Arial", 10), fg="#bdc3c7").grid(row=1, column=0, columnspan=3, pady=(5, 0))
    form_st = tk.Frame(st_center_frame, bg="#34495e")
    form_st.pack(pady=10)

    var_prefix, var_fname, var_lname = tk.StringVar(), tk.StringVar(), tk.StringVar()
    var_email, var_faculty, var_branch, var_key = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()

    st_labels = ["คำนำหน้า", "ชื่อ", "นามสกุล", "อีเมล", "คณะ", "สาขา", "รหัสกุญแจ (Key)"]
    st_vars = [var_prefix, var_fname, var_lname, var_email, var_faculty, var_branch, var_key]

    for i, (text, var) in enumerate(zip(st_labels, st_vars)):
        tk.Label(form_st, text=text+":", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=i, column=0, sticky="e", pady=6, padx=10)
        tk.Entry(form_st, textvariable=var, font=("Arial", 12), width=35).grid(row=i, column=1, pady=6, padx=10)

    # ตัวแปรเก็บ ID เป้าหมายชั่วคราว ป้องกันการเซฟทับชื่อ
    current_target_id = {"id": None}

    def search_student():
        """ฟังก์ชันค้นหาข้อมูลนักศึกษาจากฐานข้อมูล (รองรับ ID และ ชื่อ-สกุล)"""
        query_text = entry_search.get().strip()
        if not query_text:
            messagebox.showwarning("แจ้งเตือน", "กรุณากรอกรหัสนักศึกษา หรือ ชื่อ-สกุล")
            return
        
        btn_search.config(state="disabled", text="กำลังค้นหา...")

        def fetch_data_task():
            data = None
            target_id = query_text
            
            # 1. ลองค้นหาด้วย ID ก่อน (ฟังก์ชันเดิมของคุณ)
            try:
                data = get_student_by_id(query_text)
            except Exception as e:
                pass
            
            # 2. ถ้าไม่พบข้อมูล ลองค้นหาจาก ชื่อ หรือ ชื่อ-สกุล ใน Firestore
            if not data and shared_state.db is not None:
                parts = query_text.split()
                students_ref = shared_state.db.collection(Config.COLLECTION_STUDENT)
                
                try:
                    if len(parts) >= 2:
                        # ค้นหาด้วย ชื่อ และ นามสกุล
                        docs = students_ref.where(filter=FieldFilter("first_name", "==", parts[0]))\
                                           .where(filter=FieldFilter("last_name", "==", " ".join(parts[1:]))).stream()
                    else:
                        # ค้นหาด้วย ชื่อ อย่างเดียว
                        docs = students_ref.where(filter=FieldFilter("first_name", "==", query_text)).stream()
                    
                    for doc in docs:
                        data = doc.to_dict()
                        target_id = doc.id  # เก็บ Document ID / Student ID ที่พบ
                        break # ดึงข้อมูลรายการแรกที่ตรงกัน
                except Exception as e:
                    print(f"Error querying by name: {e}")
            
            def update_ui():
                btn_search.config(state="normal", text="ค้นหา")
                if data:
                    current_target_id["id"] = target_id
                    
                    # ปรับข้อความในช่องค้นหาให้กลายเป็น ID เพื่อให้ปุ่มบันทึกทำงานได้ถูกต้อง
                    entry_search.delete(0, tk.END)
                    entry_search.insert(0, target_id)
                    
                    var_prefix.set(data.get("prefix", ""))
                    var_fname.set(data.get("first_name", ""))
                    var_lname.set(data.get("last_name", ""))
                    var_email.set(data.get("email", ""))
                    var_faculty.set(data.get("faculty", ""))
                    var_branch.set(data.get("branch", ""))
                    var_key.set(data.get(Config.FIELD_NAME, ""))
                    
                    btn_save_st.config(state="normal", bg="#27ae60", cursor="hand2")
                    messagebox.showinfo("สำเร็จ", f"พบข้อมูล: {data.get('first_name')} {data.get('last_name')}")
                else:
                    messagebox.showinfo("ไม่พบข้อมูล", f"ไม่พบข้อมูล '{query_text}' ในระบบ")
                    for v in st_vars: v.set("")
                    #btn_save_st.config(state="disabled", cursor="arrow")
            
            edit_win.after(0, update_ui)

        threading.Thread(target=fetch_data_task, daemon=True).start()

    btn_search = tk.Button(search_frame, text="ค้นหา", font=("Arial", 12, "bold"), bg="#3498db", fg="white", command=search_student)
    btn_search.grid(row=0, column=2, padx=10)

    def save_student():
        """ฟังก์ชันบันทึกข้อมูลนักศึกษากลับไปยัง Firebase"""
        # ใช้ ID จากที่ระบบดึงมาได้ (ป้องกันบั๊กกรณีช่องค้นหาเป็นชื่อ)
        sid = current_target_id["id"] or entry_search.get().strip()
        if not sid: return
        
        btn_save_st.config(state="disabled", text="กำลังบันทึก...")

        def save_data_task():
            update_data = {
                "prefix": var_prefix.get().strip(),
                "first_name": var_fname.get().strip(),
                "last_name": var_lname.get().strip(),
                "email": var_email.get().strip(),
                "faculty": var_faculty.get().strip(),
                "branch": var_branch.get().strip(),
                Config.FIELD_NAME: var_key.get().strip()
            }
            success = update_student_data(sid, update_data)
            
            def update_ui():
                btn_save_st.config(state="normal", text="บันทึกการแก้ไขข้อมูลนักศึกษา")
                if success:
                    messagebox.showinfo("สำเร็จ", "อัปเดตข้อมูลนักศึกษาเรียบร้อยแล้ว")
                else:
                    messagebox.showerror("ข้อผิดพลาด", "ไม่สามารถอัปเดตข้อมูลได้")
            
            edit_win.after(0, update_ui)

        threading.Thread(target=save_data_task, daemon=True).start()

    btn_save_st = tk.Button(st_center_frame, text="บันทึกการแก้ไขข้อมูลนักศึกษา", font=("Arial", 14, "bold"), bg="#27ae60", fg="white", cursor="hand2", command=save_student)
    btn_save_st.pack(pady=10)

    # ==========================================
    # TAB 4: เพิ่มบุคคลภายนอก (External Person)
    # ==========================================
    tab_external = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_external, text=" เพิ่มบุคคลภายนอก ")

    ext_center_frame = tk.Frame(tab_external, bg="#34495e")
    ext_center_frame.pack(expand=True, fill=tk.BOTH, pady=20)

    var_ext_prefix = tk.StringVar()
    var_ext_fname = tk.StringVar()
    var_ext_lname = tk.StringVar()
    var_ext_email = tk.StringVar()

    form_ext = tk.Frame(ext_center_frame, bg="#34495e")
    form_ext.pack(pady=10)

    ext_fields = [
        ("คำนำหน้า:", var_ext_prefix),
        ("ชื่อ:", var_ext_fname),
        ("นามสกุล:", var_ext_lname),
        ("อีเมล:", var_ext_email)
    ]

    for i, (label_text, var) in enumerate(ext_fields):
        tk.Label(form_ext, text=label_text, bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=i, column=0, sticky="e", pady=8, padx=10)
        tk.Entry(form_ext, textvariable=var, font=("Arial", 12), width=35).grid(row=i, column=1, pady=8, padx=10)

    def save_external():
        prefix = var_ext_prefix.get().strip()
        fname = var_ext_fname.get().strip()
        lname = var_ext_lname.get().strip()
        email = var_ext_email.get().strip()

        if not fname:
            messagebox.showwarning("แจ้งเตือน", "กรุณากรอกชื่อผู้ใช้งานภายนอก")
            return

        btn_save_ext.config(state="disabled", text="กำลังบันทึก...")

        def save_task():
            success, result_id = add_external_person(prefix, fname, lname, email)
            
            def update_ui():
                btn_save_ext.config(state="normal", text="บันทึกข้อมูลบุคคลภายนอก")
                if success:
                    messagebox.showinfo("สำเร็จ", f"บันทึกสำเร็จ! รหัสผู้ใช้งานภายนอกคือ: {result_id}")
                    var_ext_prefix.set("")
                    var_ext_fname.set("")
                    var_ext_lname.set("")
                    var_ext_email.set("")
                else:
                    messagebox.showerror("ข้อผิดพลาด", f"ไม่สามารถบันทึกได้: {result_id}")

            edit_win.after(0, update_ui)

        threading.Thread(target=save_task, daemon=True).start()

    btn_save_ext = tk.Button(ext_center_frame, text="บันทึกข้อมูลบุคคลภายนอก", font=("Arial", 14, "bold"), bg="#27ae60", fg="white", command=save_external)
    btn_save_ext.pack(pady=20)
    
    # ==========================================
    # TAB 2: ตั้งค่าอีเมลผู้ดูแลระบบ (admin)
    # ==========================================
    tab_admin = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_admin, text=" อีเมลผู้ดูแล (admin) ")

    adm_center_frame = tk.Frame(tab_admin, bg="#34495e")
    adm_center_frame.pack(expand=True, fill=tk.BOTH, pady=30)

    var_adm_email = tk.StringVar()
    var_adm_pass = tk.StringVar()
    var_show_pass = tk.BooleanVar(value=False)

    form_adm = tk.Frame(adm_center_frame, bg="#34495e")
    form_adm.pack(pady=20)

    tk.Label(form_adm, text="อีเมลผู้ส่ง (email):", bg="#34495e", font=("Arial", 13, "bold"), fg="white").grid(row=0, column=0, sticky="e", pady=15, padx=10)
    tk.Entry(form_adm, textvariable=var_adm_email, font=("Arial", 13), width=35).grid(row=0, column=1, pady=15, padx=10)

    tk.Label(form_adm, text="รหัสผ่านแอป (App Password):", bg="#34495e", font=("Arial", 13, "bold"), fg="white").grid(row=1, column=0, sticky="e", pady=15, padx=10)
    entry_adm_pass = tk.Entry(form_adm, textvariable=var_adm_pass, font=("Arial", 13), width=35, show="*")
    entry_adm_pass.grid(row=1, column=1, pady=15, padx=10)

    def toggle_password():
        """ฟังก์ชันเปิด/ปิดการแสดงรหัสผ่านแอป"""
        if var_show_pass.get():
            entry_adm_pass.config(show="")
        else:
            entry_adm_pass.config(show="*")

    cb_show_pass = tk.Checkbutton(form_adm, text="แสดงรหัสผ่าน", variable=var_show_pass, command=toggle_password, font=("Arial", 11), bg="#34495e", fg="white", selectcolor="#2c3e50")
    cb_show_pass.grid(row=2, column=1, sticky="w", padx=10)

    def save_admin_data():
        """ฟังก์ชันบันทึกการตั้งค่าอีเมลผู้ดูแลกลับไปยัง Firebase"""
        if update_admin_email_config(var_adm_email.get(), var_adm_pass.get()):
            messagebox.showinfo("สำเร็จ", "อัปเดตข้อมูล admin เรียบร้อยแล้ว")
        else:
            messagebox.showerror("ข้อผิดพลาด", "ไม่สามารถอัปเดตข้อมูล admin ได้")

    # ปุ่มสถานะไว้แสดงผลระหว่างโหลดข้อมูล
    lbl_adm_status = tk.Label(adm_center_frame, text="กำลังดึงข้อมูล...", font=("Arial", 11, "italic"), fg="#f1c40f", bg="#34495e")
    lbl_adm_status.pack(pady=5)

    btn_save_adm = tk.Button(adm_center_frame, text="บันทึกการตั้งค่า Admin", font=("Arial", 14, "bold"), bg="#27ae60", fg="white", command=save_admin_data)
    btn_save_adm.pack(pady=20, padx=50)

    """# ==========================================
    # TAB 3: ตั้งค่าการเชื่อมต่อ BLE (connect)
    # ==========================================
    tab_connect = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_connect, text="การเชื่อมต่อ BLE ")

    conn_center_frame = tk.Frame(tab_connect, bg="#34495e")
    conn_center_frame.pack(expand=True, fill=tk.BOTH, pady=30)

    var_comp_id = tk.StringVar()
    var_uuid = tk.StringVar()

    form_conn = tk.Frame(conn_center_frame, bg="#34495e")
    form_conn.pack(pady=20)

    tk.Label(form_conn, text="Company ID (เลขฐาน 10/16):", bg="#34495e", font=("Arial", 13, "bold"), fg="white").grid(row=0, column=0, sticky="e", pady=15, padx=10)
    tk.Entry(form_conn, textvariable=var_comp_id, font=("Arial", 13), width=35).grid(row=0, column=1, pady=15, padx=10)

    tk.Label(form_conn, text="UUID (uuid):", bg="#34495e", font=("Arial", 13, "bold"), fg="white").grid(row=1, column=0, sticky="e", pady=15, padx=10)
    tk.Entry(form_conn, textvariable=var_uuid, font=("Arial", 13), width=35).grid(row=1, column=1, pady=15, padx=10)

    def save_connect_data():
        #ฟังก์ชันบันทึกการตั้งค่า BLE Config กลับไปยัง Firebase
        if update_ble_connect_config(var_comp_id.get(), var_uuid.get()):
            messagebox.showinfo("สำเร็จ", "อัปเดตข้อมูล connect (BLE) เรียบร้อยแล้ว")
        else:
            messagebox.showerror("ข้อผิดพลาด", "ไม่สามารถอัปเดตข้อมูล connect ได้")

    lbl_conn_status = tk.Label(conn_center_frame, text="กำลังดึงข้อมูล...", font=("Arial", 11, "italic"), fg="#f1c40f", bg="#34495e")
    lbl_conn_status.pack(pady=5)

    btn_save_conn = tk.Button(conn_center_frame, text="บันทึกการตั้งค่า BLE", font=("Arial", 14, "bold"), bg="#27ae60", fg="white", command=save_connect_data)
    btn_save_conn.pack(pady=20, padx=50)

    # ==========================================
    # ระบบ Asynchronous โหลดข้อมูลเมื่อเปิดหน้าต่าง
    # ป้องกันไม่ให้โปรแกรมค้างระหว่างรอ Firebase
    # ==========================================
    def load_data_background():
        # ดึงข้อมูลจากฐานข้อมูลเบื้องหลัง
        admin_data = get_admin_email_config()
        connect_data = get_ble_connect_config()

        def update_gui():
            # อัปเดตข้อมูลในหน้า Admin Tab
            if admin_data:
                var_adm_email.set(admin_data.get("email", ""))
                var_adm_pass.set(admin_data.get("emailAppPassword", ""))
                lbl_adm_status.config(text="✓ ดึงข้อมูลล่าสุดสำเร็จ", fg="#2ecc71")
            else:
                lbl_adm_status.config(text="❌ ไม่พบข้อมูลการตั้งค่า Admin", fg="#e74c3c")

            # อัปเดตข้อมูลในหน้า Connect Tab
            if connect_data:
                var_comp_id.set(str(connect_data.get("companyID", "")))
                var_uuid.set(connect_data.get("uuid", ""))
                lbl_conn_status.config(text="✓ ดึงข้อมูลล่าสุดสำเร็จ", fg="#2ecc71")
            else:
                lbl_conn_status.config(text="❌ ไม่พบข้อมูลการตั้งค่า Connect", fg="#e74c3c")
        
        # ส่งคำสั่งไปรันอัปเดต GUI ใน Main Thread
        edit_win.after(0, update_gui)

    # สั่งให้ทำงานใน Thread แยกต่างหากทันที
    threading.Thread(target=load_data_background, daemon=True).start()"""


def open_admin_window(root):
    """
    หน้าต่างเมนูหลักของผู้ดูแลระบบ (Admin Menu)
    สำหรับเลือกเข้าถึงฟังก์ชันต่างๆ เช่น ดูแดชบอร์ด, นำเข้าข้อมูล CSV และการตั้งค่าระบบ
    """
    admin_window = tk.Toplevel(root)
    admin_window.title("ระบบผู้ดูแลระบบ (Admin Menu) - กด F11 เพื่อเต็มจอ")
    admin_window.geometry("800x650")
    admin_window.configure(bg="#34495e")
    admin_window.resizable(True, True)
    bind_fullscreen(admin_window)

    # จัดกึ่งกลาง
    center_frame = tk.Frame(admin_window, bg="#34495e")
    center_frame.pack(expand=True)

    lbl_admin = tk.Label(
        center_frame, text="เมนูผู้ดูแลระบบ", font=("Arial", 36, "bold"), fg="white", bg="#34495e"
    )
    lbl_admin.pack(pady=(10, 30))

    # 1. ปุ่มสำหรับแสดงแดชบอร์ด
    btn_dashboard = tk.Button(
        center_frame,
        text="แดชบอร์ดสถิติการเข้าใช้งาน",
        font=("Arial", 16, "bold"),
        bg="#3498db",
        fg="white",
        pady=12,
        width=35,
        command=show_dashboard_graph,
    )
    btn_dashboard.pack(pady=10)

    # 2. ปุ่มนำเข้าข้อมูลรายชื่อผู้ใช้งาน
    btn_import = tk.Button(
        center_frame,
        text="นำเข้าข้อมูลผู้ใช้งาน (CSV)",
        font=("Arial", 16, "bold"),
        bg="#27ae60",
        fg="white",
        pady=12,
        width=35,
        command=import_csv_to_firebase,
    )
    btn_import.pack(pady=10)

    # 3. ปุ่มนำเข้าประวัติการใช้งาน
    btn_import_attendance = tk.Button(
        center_frame,
        text="นำเข้าประวัติการเข้าใช้งาน(CSV)(ใช้สำหรับทดสอบ)",
        font=("Arial", 16, "bold"),
        bg="#8e44ad",
        fg="white",
        pady=12,
        width=35,
        command=import_attendance_csv_to_firebase,
    )
    btn_import_attendance.pack(pady=10)

    # 4. ปุ่มเปิดหน้าต่างแก้ไขข้อมูลและตั้งค่าระบบ
    btn_edit = tk.Button(
        center_frame,
        text="ตั้งค่าระบบและแก้ไขข้อมูลบุคคล",
        font=("Arial", 16, "bold"),
        bg="#d35400",
        fg="white",
        pady=12,
        width=35,
        command=lambda: open_edit_window(admin_window)
    )
    btn_edit.pack(pady=10)

    tk.Label(center_frame, text="* สามารถกดปุ่ม F11 เพื่อเปิด/ปิด โหมดเต็มหน้าจอได้ *", font=("Arial", 12), fg="#bdc3c7", bg="#34495e").pack(pady=20)


def setup_gui():
    """
    ฟังก์ชันสำหรับสร้างหน้าต่างหลัก (Main Window)
    รับหน้าที่แสดงสถานะของประตู (Locked/Unlocked) และผู้ที่ใช้งานล่าสุด
    """
    root = tk.Tk()
    root.title("หน้าจอแสดงสถานะประตู - กด F11 เพื่อเต็มจอ")
    root.geometry("800x700")
    root.configure(bg="#2c3e50")
    root.resizable(True, True)
    bind_fullscreen(root)

    # จัดกึ่งกลางหน้าจอ
    main_frame = tk.Frame(root, bg="#2c3e50")
    main_frame.pack(expand=True)

    lbl_title = tk.Label(
        main_frame, text="สถานะระบบประตู", font=("Arial", 40, "bold"), fg="white", bg="#2c3e50"
    )
    lbl_title.pack(pady=(20, 20))

    # Canvas สำหรับวาดวงกลมไฟสถานะ
    canvas = tk.Canvas(main_frame, width=220, height=220, bg="#2c3e50", highlightthickness=0)
    canvas.pack(pady=20)

    light_circle = canvas.create_oval(
        10, 10, 210, 210, fill="#e74c3c", outline="#c0392b", width=8
    )

    lbl_status = tk.Label(
        main_frame, text="LOCKED", font=("Arial", 30, "bold"), fg="#e74c3c", bg="#2c3e50"
    )
    lbl_status.pack(pady=(10, 10))

    lbl_action = tk.Label(
        main_frame, text="", font=("Arial", 28, "bold"), fg="#f1c40f", bg="#2c3e50"
    )
    lbl_action.pack()

    lbl_user = tk.Label(
        main_frame, text="", font=("Arial", 30), fg="white", bg="#2c3e50"
    )
    lbl_user.pack(pady=(0, 20))

    # ปุ่มกดเข้าสู่เมนูจัดการของผู้ดูแลระบบ (Admin)
    btn_admin_menu = tk.Button(
        main_frame,
        text="ตั้งค่าระบบ / จัดการข้อมูล (Admin Menu)",
        font=("Arial", 16, "bold"),
        bg="#7f8c8d",
        fg="white",
        padx=20,
        pady=10,
        command=lambda: open_admin_window(root)
    )
    btn_admin_menu.pack(pady=20)

    def update_gui():
        """ฟังก์ชัน Loop สำหรับอัปเดตสีไฟสถานะและข้อความบนหน้าจอหลักตลอดเวลา"""
        if shared_state.gui_light_state == "green":
            canvas.itemconfig(light_circle, fill="#2ecc71", outline="#27ae60")
            lbl_status.config(text="ประตูเปิด", fg="#2ecc71")
            lbl_action.config(text=shared_state.gui_action_text)
            lbl_user.config(text=shared_state.gui_user_name)
        else:
            canvas.itemconfig(light_circle, fill="#e74c3c", outline="#c0392b")
            lbl_status.config(text="ประตูปิด", fg="#e74c3c")
            lbl_action.config(text="")
            lbl_user.config(text="")

        root.after(100, update_gui)

    update_gui()
    return root


if __name__ == "__main__":
    # เปิดการสแกน BLE ทำงานเป็น Background
    bg_thread = threading.Thread(target=run_background_scanner, daemon=True)
    bg_thread.start()

    # สร้างและรันหน้าจอ GUI หลัก
    gui_window = setup_gui()
    gui_window.mainloop()
