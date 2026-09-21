import threading
import tkinter as tk
from tkinter import ttk, messagebox
import re
import unicodedata

from PIL import ImageTk
from thai_font import create_thai_text_image

from google.cloud.firestore_v1.base_query import FieldFilter

import shared_state
from ble_scanner import run_background_scanner
from dashboard import show_dashboard_graph, manual_refresh_dashboard_cache
from db_manager import (
    import_csv_to_firebase,
    import_attendance_csv_to_firebase,
    get_member_by_id,
    update_member_data,
    get_admin_email_config,
    update_admin_email_config,
    get_ble_connect_config,
    update_ble_connect_config,
    add_external_person,
    add_single_student,
    get_faculties_and_branches,
    count_members_by_prefix,
    delete_members_by_prefix,
    find_member_for_deletion,
    delete_member_by_exact_id,
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

def handle_manual_refresh_cache():
    """เรียกจากปุ่ม 'อัปเดตแคชตอนนี้' - บังคับรีเฟรชแคชแดชบอร์ด (สมาชิก attendance)
    ทันที แล้วโชว์สรุปผลให้เห็นชัดเจนว่าทำงานจริง ไม่ต้องเดาเหมือน background thread
    ที่รันเงียบ ๆ อยู่เบื้องหลังทุก 60 วิ"""
    success, message = manual_refresh_dashboard_cache()
    if success:
        messagebox.showinfo("อัปเดตแคช", message)
    else:
        messagebox.showwarning("อัปเดตแคช", message)

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
    # TAB 1: จัดการข้อมูลสมาชิก (student)
    # ==========================================
    tab_student = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_student, text="แก้ไขข้อมูลสมาชิก")

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
            messagebox.showwarning("แจ้งเตือน", "กรุณากรอกรหัสสมาชิก หรือ ชื่อ-สกุล")
            return
        
        btn_search.config(state="disabled", text="กำลังค้นหา...")

        def fetch_data_task():
            data = None
            target_id = query_text
            
            # 1. ลองค้นหาด้วย ID ก่อน (ฟังก์ชันเดิมของคุณ)
            try:
                data = get_member_by_id(query_text)
            except Exception as e:
                pass
            
            # 2. ถ้าไม่พบข้อมูล ลองค้นหาจาก ชื่อ หรือ ชื่อ-สกุล ใน Firestore
            if not data and shared_state.db is not None:
                parts = query_text.split()
                students_ref = shared_state.db.collection(Config.COLLECTION_MEMBER)
                
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
        member_id = current_target_id["id"] or entry_search.get().strip()
        if not member_id: return
        
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
            success = update_member_data(member_id, update_data)
            
            def update_ui():
                btn_save_st.config(state="normal", text="บันทึกการแก้ไขข้อมูลสมาชิก")
                if success:
                    messagebox.showinfo("สำเร็จ", "อัปเดตข้อมูลสมาชิกเรียบร้อยแล้ว")
                else:
                    messagebox.showerror("ข้อผิดพลาด", "ไม่สามารถอัปเดตข้อมูลได้")
            
            edit_win.after(0, update_ui)

        threading.Thread(target=save_data_task, daemon=True).start()

    btn_save_st = tk.Button(st_center_frame, text="บันทึกการแก้ไขข้อมูลสมาชิก", font=("Arial", 14, "bold"), bg="#27ae60", fg="white", cursor="hand2", command=save_student)
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

    def valid_person_name(value):
        """รับเฉพาะตัวอักษร ช่องว่าง และขีดกลางสำหรับชื่อบุคคล"""
        return all(
            character.isalpha()
            or character.isspace()
            or character == "-"
            or unicodedata.category(character).startswith("M")
            for character in value
        )

    name_validation = (edit_win.register(valid_person_name), "%P")
    email_validation = (
        edit_win.register(lambda value: not any(character.isspace() for character in value)),
        "%P",
    )

    ext_fields = [
        ("คำนำหน้า *:", var_ext_prefix),
        ("ชื่อ *:", var_ext_fname),
        ("นามสกุล *:", var_ext_lname),
        ("อีเมล:", var_ext_email)
    ]

    for i, (label_text, var) in enumerate(ext_fields):
        tk.Label(form_ext, text=label_text, bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=i, column=0, sticky="e", pady=8, padx=10)
        entry_options = {
            "font": ("Arial", 12),
            "width": 35,
        }
        if i < 3:
            entry_options.update(
                validate="key",
                validatecommand=name_validation,
            )
        else:
            entry_options.update(
                validate="key",
                validatecommand=email_validation,
            )
        tk.Entry(form_ext, textvariable=var, **entry_options).grid(row=i, column=1, pady=8, padx=10)

    lbl_external_result = tk.Label(
        ext_center_frame,
        text="",
        bg="#34495e",
        fg="#f1c40f",
        font=("Arial", 16, "bold"),
    )

    def save_external():
        prefix = var_ext_prefix.get().strip()
        fname = var_ext_fname.get().strip()
        lname = var_ext_lname.get().strip()
        email = var_ext_email.get().strip()

        missing_fields = []
        if not prefix:
            missing_fields.append("คำนำหน้า")
        if not fname:
            missing_fields.append("ชื่อ")
        if not lname:
            missing_fields.append("นามสกุล")

        if missing_fields:
            messagebox.showwarning(
                "ข้อมูลไม่ครบ",
                f"กรุณากรอก: {', '.join(missing_fields)}",
            )
            return

        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            messagebox.showwarning("รูปแบบไม่ถูกต้อง", "กรุณากรอกอีเมลให้ถูกต้อง เช่น name@example.com")
            return

        btn_save_ext.config(state="disabled", text="กำลังบันทึก...")

        def save_task():
            success, result_id = add_external_person(prefix, fname, lname, email)
            
            def update_ui():
                btn_save_ext.config(state="normal", text="บันทึกข้อมูลบุคคลภายนอก")
                if success:
                    lbl_external_result.config(
                        text=f"บันทึกสำเร็จ\nรหัสสมาชิกภายนอก: {result_id}",
                        fg="#f1c40f",
                    )
                    lbl_external_result.pack(pady=(5, 0))
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
    # TAB: เพิ่มนักศึกษารายบุคคล (Add Single Student)
    # ==========================================
    tab_add_student = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_add_student, text=" เพิ่มสมาชิก (นักศึกษา) ")

    add_st_center_frame = tk.Frame(tab_add_student, bg="#34495e")
    add_st_center_frame.pack(expand=True, fill=tk.BOTH, pady=15, padx=20)

    var_add_st_id = tk.StringVar()
    var_add_st_prefix = tk.StringVar()
    var_add_st_fname = tk.StringVar()
    var_add_st_lname = tk.StringVar()
    var_add_st_faculty = tk.StringVar()
    var_add_st_branch = tk.StringVar()
    var_add_st_email_preview = tk.StringVar(value="@student.sru.ac.th")

    # อัปเดตพรีวิวอีเมลอัตโนมัติ
    def update_email_preview(*args):
        st_id = var_add_st_id.get().strip()
        var_add_st_email_preview.set(f"{st_id}@student.sru.ac.th" if st_id else "@student.sru.ac.th")

    var_add_st_id.trace_add("write", update_email_preview)

    form_add_st = tk.Frame(add_st_center_frame, bg="#34495e")
    form_add_st.pack(pady=10)

    # ตัวตรวจสอบ: รหัสนักศึกษาต้องเป็นตัวเลขเท่านั้น
    digit_validation = (
        edit_win.register(lambda val: val.isdigit() or val == ""),
        "%P"
    )

    # 1. รหัสนักศึกษา
    tk.Label(form_add_st, text="รหัสนักศึกษา *:", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=0, column=0, sticky="e", pady=6, padx=10)
    tk.Entry(form_add_st, textvariable=var_add_st_id, font=("Arial", 12), width=35, validate="key", validatecommand=digit_validation).grid(row=0, column=1, pady=6, padx=10)

    # 2. คำนำหน้า
    tk.Label(form_add_st, text="คำนำหน้า *:", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=1, column=0, sticky="e", pady=6, padx=10)
    tk.Entry(form_add_st, textvariable=var_add_st_prefix, font=("Arial", 12), width=35, validate="key", validatecommand=name_validation).grid(row=1, column=1, pady=6, padx=10)

    # 3. ชื่อ
    tk.Label(form_add_st, text="ชื่อ *:", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=2, column=0, sticky="e", pady=6, padx=10)
    tk.Entry(form_add_st, textvariable=var_add_st_fname, font=("Arial", 12), width=35, validate="key", validatecommand=name_validation).grid(row=2, column=1, pady=6, padx=10)

    # 4. นามสกุล
    tk.Label(form_add_st, text="นามสกุล *:", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=3, column=0, sticky="e", pady=6, padx=10)
    tk.Entry(form_add_st, textvariable=var_add_st_lname, font=("Arial", 12), width=35, validate="key", validatecommand=name_validation).grid(row=3, column=1, pady=6, padx=10)

    # --- ดึงข้อมูลคณะ/สาขาจาก Firestore เพื่อสร้าง Dropdown ---
    faculties_list, faculty_branches_map = get_faculties_and_branches()

    # 5. คณะ (Combobox)
    tk.Label(form_add_st, text="คณะ *:", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=4, column=0, sticky="e", pady=6, padx=10)
    combo_faculty = ttk.Combobox(form_add_st, textvariable=var_add_st_faculty, values=faculties_list, font=("Arial", 11), width=33, state="readonly")
    combo_faculty.grid(row=4, column=1, pady=6, padx=10)

    # 6. สาขา (Combobox)
    tk.Label(form_add_st, text="สาขา *:", bg="#34495e", font=("Arial", 12, "bold"), fg="#ecf0f1").grid(row=5, column=0, sticky="e", pady=6, padx=10)
    combo_branch = ttk.Combobox(form_add_st, textvariable=var_add_st_branch, font=("Arial", 11), width=33, state="readonly")
    combo_branch.grid(row=5, column=1, pady=6, padx=10)

    # ฟังก์ชันเปลี่ยนรายการสาขาตามคณะที่เลือก
    def on_faculty_select(event):
        selected_fac = var_add_st_faculty.get()
        branches = faculty_branches_map.get(selected_fac, [])
        combo_branch['values'] = branches
        var_add_st_branch.set("")  # ล้างค่าสาขาเดิมเมื่อเปลี่ยนคณะ

    combo_faculty.bind("<<ComboboxSelected>>", on_faculty_select)

    # แสดงพรีวิวอีเมลอัตโนมัติ
    tk.Label(form_add_st, text="อีเมล (สร้างให้ออโต้):", bg="#34495e", font=("Arial", 11, "italic"), fg="#bdc3c7").grid(row=6, column=0, sticky="e", pady=4, padx=10)
    tk.Label(form_add_st, textvariable=var_add_st_email_preview, bg="#34495e", font=("Arial", 11, "bold"), fg="#f1c40f").grid(row=6, column=1, sticky="w", pady=4, padx=10)

    lbl_add_st_result = tk.Label(add_st_center_frame, text="", bg="#34495e", fg="#2ecc71", font=("Arial", 12, "bold"))
    lbl_add_st_result.pack(pady=5)

    def save_single_student():
        st_id = var_add_st_id.get().strip()
        prefix = var_add_st_prefix.get().strip()
        fname = var_add_st_fname.get().strip()
        lname = var_add_st_lname.get().strip()
        faculty = var_add_st_faculty.get().strip()
        branch = var_add_st_branch.get().strip()

        # ตรวจสอบการเลือกข้อมูล
        missing_fields = []
        if not st_id: missing_fields.append("รหัสนักศึกษา")
        if not prefix: missing_fields.append("คำนำหน้า")
        if not fname: missing_fields.append("ชื่อ")
        if not lname: missing_fields.append("นามสกุล")
        if not faculty: missing_fields.append("คณะ")
        if not branch: missing_fields.append("สาขา")

        if missing_fields:
            messagebox.showwarning("ข้อมูลไม่ครบถ้วน", f"กรุณากรอกและเลือกข้อมูลให้ครบถ้วน:\n- {', '.join(missing_fields)}")
            return

        if not st_id.isdigit() or len(st_id) < 8 or len(st_id) > 13:
            messagebox.showwarning("รูปแบบไม่ถูกต้อง", "รหัสนักศึกษาต้องเป็นตัวเลข 8 ถึง 13 หลัก")
            return

        btn_save_add_st.config(state="disabled", text="กำลังบันทึก...")

        def save_task():
            success, msg = add_single_student(st_id, prefix, fname, lname, faculty, branch)
            
            def update_ui():
                btn_save_add_st.config(state="normal", text="บันทึกข้อมูลนักศึกษา")
                if success:
                    lbl_add_st_result.config(text=f"✓ {msg}", fg="#2ecc71")
                    # ล้างค่าหน้าฟอร์มเมื่อบันทึกสำเร็จ
                    var_add_st_id.set("")
                    var_add_st_prefix.set("")
                    var_add_st_fname.set("")
                    var_add_st_lname.set("")
                    var_add_st_faculty.set("")
                    var_add_st_branch.set("")
                    combo_branch['values'] = []  # ล้างตัวเลือกสาขา
                else:
                    lbl_add_st_result.config(text="", fg="#2ecc71")
                    messagebox.showerror("ข้อผิดพลาด", msg)

            edit_win.after(0, update_ui)

        threading.Thread(target=save_task, daemon=True).start()

    btn_save_add_st = tk.Button(add_st_center_frame, text="บันทึกข้อมูลนักศึกษา", font=("Arial", 14, "bold"), bg="#27ae60", fg="white", cursor="hand2", command=save_single_student)
    btn_save_add_st.pack(pady=10)

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

    # ==========================================
    # ระบบ Asynchronous โหลดข้อมูลเมื่อเปิดหน้าต่าง
    # ป้องกันไม่ให้โปรแกรมค้างระหว่างรอ Firebase
    # ==========================================
    def load_data_background():
        # ดึงข้อมูลจากฐานข้อมูลเบื้องหลัง
        admin_data = get_admin_email_config()

        def update_gui():
            # อัปเดตข้อมูลในหน้า Admin Tab
            if admin_data:
                var_adm_email.set(admin_data.get("email", ""))
                var_adm_pass.set(admin_data.get("emailAppPassword", ""))
                lbl_adm_status.config(text="✓ ดึงข้อมูลล่าสุดสำเร็จ", fg="#2ecc71")
            else:
                lbl_adm_status.config(text="❌ ไม่พบข้อมูลการตั้งค่า Admin", fg="#e74c3c")
        
        # ส่งคำสั่งไปรันอัปเดต GUI ใน Main Thread
        edit_win.after(0, update_gui)

    # สั่งให้ทำงานใน Thread แยกต่างหากทันที
    threading.Thread(target=load_data_background, daemon=True).start()

    # ==========================================
    # TAB 5: ลบข้อมูลสมาชิก (Delete Members)
    # ==========================================
    tab_delete = tk.Frame(notebook, bg="#34495e")
    notebook.add(tab_delete, text=" ลบข้อมูลสมาชิก ")

    del_center_frame = tk.Frame(tab_delete, bg="#34495e")
    del_center_frame.pack(expand=True, fill=tk.BOTH, pady=15, padx=20)

    # --- 1. ลบแบบกลุ่ม ---
    group_del_box = tk.LabelFrame(del_center_frame, text=" 1. เลือกลบแบบกลุ่ม (จากรหัส 2 ตัวหน้า) ", font=("Arial", 12, "bold"), bg="#34495e", fg="#e74c3c", bd=2)
    group_del_box.pack(fill=tk.X, pady=10, padx=10, ipady=5)

    tk.Label(group_del_box, text="ระบุรหัส 2 ตัวหน้า (คั่นด้วยเครื่องหมายจุลภาค , เช่น 66,67,68):", bg="#34495e", fg="white", font=("Arial", 11)).pack(anchor="w", padx=15, pady=(5, 2))
    var_group_prefix = tk.StringVar()
    prefix_char_validation = (
        edit_win.register(lambda value: all(c.isdigit() or c in ", " for c in value)),
        "%P",
    )
    entry_group_prefix = tk.Entry(
        group_del_box, textvariable=var_group_prefix, font=("Arial", 12), width=40,
        validate="key", validatecommand=prefix_char_validation,
    )
    entry_group_prefix.pack(padx=15, pady=5, anchor="w")

    def action_delete_group():
        prefix_val = var_group_prefix.get().strip()
        if not prefix_val:
            messagebox.showwarning("แจ้งเตือน", "กรุณาระบุรหัส 2 ตัวหน้าที่ต้องการลบ")
            return

        btn_del_group.config(state="disabled", text="กำลังตรวจสอบจำนวน...")

        def task_check_then_delete():
            # ขั้นที่ 1: ตรวจสอบรูปแบบ + นับจำนวนที่ตรงเงื่อนไขก่อน ยังไม่ลบจริง
            ok, count, msg = count_members_by_prefix(prefix_val)

            def after_count():
                if not ok:
                    btn_del_group.config(state="normal", text="ลบข้อมูลแบบกลุ่ม")
                    messagebox.showerror("รูปแบบไม่ถูกต้อง", msg)
                    return

                if count == 0:
                    btn_del_group.config(state="normal", text="ลบข้อมูลแบบกลุ่ม")
                    messagebox.showinfo("ไม่พบข้อมูล", f"ไม่พบสมาชิกที่มีรหัสขึ้นต้นด้วย [{prefix_val}]")
                    return

                # ขั้นที่ 2: บอกจำนวนจริงที่พบ แล้วให้ยืนยันอีกครั้งก่อนลบจริง
                confirmed = messagebox.askyesno(
                    "ยืนยันการลบแบบกลุ่ม",
                    f"พบสมาชิกที่ตรงเงื่อนไข [{prefix_val}] อยู่ {count} คนในระบบขณะนี้\n\n"
                    f"ต้องการลบทั้งหมด {count} คนนี้หรือไม่?\n\n"
                    "⚠️ การกระทำนี้ไม่สามารถย้อนกลับได้!",
                    icon="warning",
                )
                if not confirmed:
                    btn_del_group.config(state="normal", text="ลบข้อมูลแบบกลุ่ม")
                    return

                btn_del_group.config(text="กำลังลบข้อมูล...")

                def task_delete():
                    success, del_count, del_msg = delete_members_by_prefix(prefix_val)
                    def after_delete():
                        btn_del_group.config(state="normal", text="ลบข้อมูลแบบกลุ่ม")
                        if success:
                            messagebox.showinfo("ผลการทำงาน", del_msg)
                            var_group_prefix.set("")
                        else:
                            messagebox.showerror("ข้อผิดพลาด", f"ไม่สามารถลบข้อมูลได้: {del_msg}")
                    edit_win.after(0, after_delete)

                threading.Thread(target=task_delete, daemon=True).start()

            edit_win.after(0, after_count)

        threading.Thread(target=task_check_then_delete, daemon=True).start()

    btn_del_group = tk.Button(group_del_box, text="ลบข้อมูลแบบกลุ่ม", font=("Arial", 11, "bold"), bg="#c0392b", fg="white", cursor="hand2", command=action_delete_group)
    btn_del_group.pack(anchor="w", padx=15, pady=8)

    # --- 2. ลบรายบุคคล ---
    single_del_box = tk.LabelFrame(del_center_frame, text=" 2. เลือกลบรายบุคคล ", font=("Arial", 12, "bold"), bg="#34495e", fg="#e74c3c", bd=2)
    single_del_box.pack(fill=tk.X, pady=10, padx=10, ipady=5)

    tk.Label(single_del_box, text="ระบุรหัสสมาชิก หรือ ชื่อ-นามสกุล (เช่น 66010001 หรือ สมชาย ใจดี):", bg="#34495e", fg="white", font=("Arial", 11)).pack(anchor="w", padx=15, pady=(5, 2))

    single_del_input_row = tk.Frame(single_del_box, bg="#34495e")
    single_del_input_row.pack(padx=15, pady=5, anchor="w", fill=tk.X)

    var_single_del = tk.StringVar()
    entry_single_del = tk.Entry(single_del_input_row, textvariable=var_single_del, font=("Arial", 12), width=32)
    entry_single_del.grid(row=0, column=0, padx=(0, 10))

    lbl_single_preview = tk.Label(
        single_del_box, text="ยังไม่ได้ค้นหา - กด 'ค้นหา' ก่อนลบทุกครั้ง เพื่อยืนยันว่าเป็นคนที่ต้องการจริง",
        bg="#34495e", fg="#bdc3c7", font=("Arial", 11), justify="left", anchor="w",
    )
    lbl_single_preview.pack(anchor="w", padx=15, pady=(2, 5), fill=tk.X)

    # เก็บผลการค้นหาล่าสุดไว้ ป้องกันลบผิดคนถ้าผลลัพธ์เปลี่ยนระหว่างค้นหา-กดลบ
    single_del_state = {"member_id": None, "display": None}

    def action_search_single():
        target_val = var_single_del.get().strip()
        if not target_val:
            messagebox.showwarning("แจ้งเตือน", "กรุณากรอกรหัสสมาชิก หรือ ชื่อ-นามสกุล")
            return

        single_del_state["member_id"] = None
        single_del_state["display"] = None
        btn_del_single.config(state="disabled")
        btn_search_single.config(state="disabled", text="กำลังค้นหา...")
        lbl_single_preview.config(text="กำลังค้นหา...", fg="#f1c40f")

        def task_search():
            ok, matches, msg = find_member_for_deletion(target_val)

            def after_search():
                btn_search_single.config(state="normal", text="ค้นหา")

                if not ok:
                    lbl_single_preview.config(text=f"❌ {msg}", fg="#e74c3c")
                    return

                if len(matches) == 1:
                    m = matches[0]
                    display = f"{m['prefix']}{m['first_name']} {m['last_name']}".strip()
                    lbl_single_preview.config(
                        text=(
                            f"✓ พบข้อมูล: {display}\n"
                            f"   รหัสสมาชิก: {m['member_id']}  |  คณะ: {m['faculty']}  |  สาขา: {m['branch']}"
                        ),
                        fg="#2ecc71",
                    )
                    single_del_state["member_id"] = m["member_id"]
                    single_del_state["display"] = display
                    btn_del_single.config(state="normal")
                else:
                    # พบมากกว่า 1 คน - ไม่ลบอัตโนมัติ ให้ระบุรหัสสมาชิกที่แน่ชัดแทน
                    lines = [
                        f"   - {m['member_id']}: {m['prefix']}{m['first_name']} {m['last_name']} ({m['faculty']})"
                        for m in matches[:10]
                    ]
                    lbl_single_preview.config(
                        text=(
                            f"⚠️ พบข้อมูลตรงกัน {len(matches)} คน กรุณาระบุรหัสสมาชิกให้ชัดเจนแทนการค้นหาด้วยชื่อ:\n"
                            + "\n".join(lines)
                        ),
                        fg="#f39c12",
                    )
                    btn_del_single.config(state="disabled")

            edit_win.after(0, after_search)

        threading.Thread(target=task_search, daemon=True).start()

    btn_search_single = tk.Button(single_del_input_row, text="ค้นหา", font=("Arial", 11, "bold"), bg="#3498db", fg="white", cursor="hand2", command=action_search_single)
    btn_search_single.grid(row=0, column=1)

    def action_delete_single():
        member_id = single_del_state.get("member_id")
        display = single_del_state.get("display") or member_id
        if not member_id:
            messagebox.showwarning("แจ้งเตือน", "กรุณากด 'ค้นหา' และเลือกยืนยันตัวตนก่อนลบ")
            return

        if not messagebox.askyesno(
            "ยืนยันการลบรายบุคคล",
            f"คุณแน่ใจหรือไม่ว่าต้องการลบข้อมูลของ:\n\n{display}\nรหัสสมาชิก: {member_id}\n\n"
            "⚠️ การกระทำนี้ไม่สามารถย้อนกลับได้!",
            icon="warning",
        ):
            return

        btn_del_single.config(state="disabled", text="กำลังลบข้อมูล...")

        def task_del_single():
            success, msg = delete_member_by_exact_id(member_id)
            def update_ui():
                btn_del_single.config(state="disabled", text="ลบข้อมูลรายบุคคล")
                if success:
                    messagebox.showinfo("ผลการทำงาน", msg)
                    var_single_del.set("")
                    lbl_single_preview.config(text="ยังไม่ได้ค้นหา - กด 'ค้นหา' ก่อนลบทุกครั้ง เพื่อยืนยันว่าเป็นคนที่ต้องการจริง", fg="#bdc3c7")
                    single_del_state["member_id"] = None
                    single_del_state["display"] = None
                else:
                    messagebox.showerror("ข้อผิดพลาด", f"ไม่สามารถลบข้อมูลได้: {msg}")
            edit_win.after(0, update_ui)

        threading.Thread(target=task_del_single, daemon=True).start()

    btn_del_single = tk.Button(single_del_box, text="ลบข้อมูลรายบุคคล", font=("Arial", 11, "bold"), bg="#c0392b", fg="white", cursor="hand2", state="disabled", command=action_delete_single)
    btn_del_single.pack(anchor="w", padx=15, pady=8)

def open_admin_window(root):
    """
    หน้าต่างเมนูหลักของผู้ดูแลระบบ (Admin Menu)
    สำหรับเลือกเข้าถึงฟังก์ชันต่างๆ เช่น ดูแดชบอร์ด, นำเข้าข้อมูล CSV และการตั้งค่าระบบ
    """
    admin_window = tk.Toplevel(root)
    admin_window.title("ตั้งค่าระบบ / จัดการข้อมูล - กด F11 เพื่อเต็มจอ")
    admin_window.geometry("800x650")
    admin_window.configure(bg="#34495e")
    admin_window.resizable(True, True)
    bind_fullscreen(admin_window)

    # จัดกึ่งกลาง
    center_frame = tk.Frame(admin_window, bg="#34495e")
    center_frame.pack(expand=True)

    admin_title_image = create_thai_text_image(
        "ตั้งค่าระบบ / จัดการข้อมูล",
        font_size=36,
        text_color="white",
        background="#34495e",
        padding_x=8,
        padding_y=4,
    )
    admin_title_image = ImageTk.PhotoImage(admin_title_image)
    lbl_admin = tk.Label(
        center_frame,
        image=admin_title_image,
        bg="#34495e",
        borderwidth=0,
        highlightthickness=0,
    )
    lbl_admin.image = admin_title_image
    lbl_admin.pack(pady=(10, 30))

    menu_width = 446
    menu_height = 56

    def create_menu_button(text, background, command, busy_text, background_task=True):
        def make_image(label):
            image = create_thai_text_image(
                label,
                font_size=25,
                text_color="white",
                background=background,
                padding_x=12,
                padding_y=12,
                fixed_width=menu_width - 2,
                fixed_height=menu_height - 2,
            )
            return ImageTk.PhotoImage(image)

        image = make_image(text)
        busy_image = make_image(busy_text)
        button = tk.Button(
            center_frame,
            image=image,
            bg=background,
            activebackground=background,
            borderwidth=1,
            relief="raised",
            padx=0,
            pady=0,
            highlightthickness=0,
            command=command,
        )
        button.image = image
        button.busy_image = busy_image
        button.pack(pady=10)

        def run_command_with_status():
            button.config(image=button.busy_image, state="disabled", cursor="watch")

            if not background_task:
                command()
                button.config(image=button.image, state="normal", cursor="hand2")
                return

            def worker():
                try:
                    result = command()
                    if isinstance(result, threading.Thread):
                        result.join()
                finally:
                    def restore_button():
                        if button.winfo_exists():
                            button.config(
                                image=button.image,
                                state="normal",
                                cursor="hand2",
                            )

                    if root.winfo_exists():
                        root.after(0, restore_button)

            threading.Thread(target=worker, daemon=True).start()

        button.config(command=run_command_with_status, cursor="hand2")
        return button

    create_menu_button(
        "แดชบอร์ดสถิติการเข้าใช้งาน", "#3498db", show_dashboard_graph,
        "กำลังโหลดแดชบอร์ด...",
    )
    create_menu_button(
        "อัปเดตแคช", "#16a085", handle_manual_refresh_cache,
        "กำลังอัปเดตแคช...",
    )
    create_menu_button(
        "นำเข้าข้อมูลสมาชิก (CSV)", "#27ae60", import_csv_to_firebase,
        "กำลังนำเข้าข้อมูล...",
    )
    create_menu_button(
        "นำเข้าประวัติการเข้าใช้งาน(CSV)(ใช้สำหรับทดสอบ)",
        "#8e44ad",
        import_attendance_csv_to_firebase,
        "กำลังนำเข้าประวัติ...",
    )
    create_menu_button(
        "ตั้งค่าระบบและจัดการข้อมูลสมาชิก",
        "#d35400",
        lambda: open_edit_window(admin_window),
        "กำลังเปิดหน้าต่าง...",
        background_task=False,
    )

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

    admin_menu_image = create_thai_text_image(
        "ตั้งค่าระบบ / จัดการข้อมูล",
        font_size=25,
        text_color="white",
        background="#7f8c8d",
        padding_x=16,
        padding_y=14,
    )
    admin_menu_image = ImageTk.PhotoImage(admin_menu_image)

    # ปุ่มกดเข้าสู่เมนูจัดการของผู้ดูแลระบบ (Admin)
    btn_admin_menu = tk.Button(
        main_frame,
        image=admin_menu_image,
        bg="#7f8c8d",
        activebackground="#7f8c8d",
        borderwidth=1,
        relief="raised",
        padx=0,
        pady=0,
        highlightthickness=0,
        command=lambda: open_admin_window(root)
    )
    btn_admin_menu.image = admin_menu_image
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