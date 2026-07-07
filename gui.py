import os
import json
import queue
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox
import customtkinter as ctk
from downloader import NDEDownloader

# Atur penampilan default customtkinter
ctk.set_appearance_mode("dark")  # Mode: "System", "Dark", "Light"
ctk.set_default_color_theme("blue")  # Tema: "blue", "green", "dark-blue"

CONFIG_FILE = Path(__file__).parent / "config.json"

class NDEDownloaderGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Konfigurasi Window
        self.title("NDE Downloader - Pos Indonesia")
        self.geometry("900x700")
        self.minsize(800, 600)
        
        # Variable state
        self.running_thread = None
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.downloader = None
        
        # Memuat konfigurasi sebelumnya
        self.config = self.load_config()
        
        # Grid layout 2x1 (Sidebar + Main)
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=3)
        self.grid_rowconfigure(0, weight=1)
        
        # Inisialisasi UI
        self.create_sidebar()
        self.create_main_content()
        self.load_saved_data()
        
        # Memulai thread-safe log poller
        self.poll_logs()

    def create_sidebar(self):
        # Sidebar Frame (Non-scrollable, fits 1 screen)
        self.sidebar_frame = ctk.CTkFrame(self, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        
        # App Title
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="NDE Downloader", font=ctk.CTkFont(size=18, weight="bold"))
        self.logo_label.pack(padx=10, pady=(15, 2))
        
        self.sub_logo = ctk.CTkLabel(self.sidebar_frame, text="Otomasi Unduh Dokumen", font=ctk.CTkFont(size=11, slant="italic"))
        self.sub_logo.pack(padx=10, pady=(0, 15))
        
        # 1. Kredensial Login
        self.cred_frame = ctk.CTkFrame(self.sidebar_frame)
        self.cred_frame.pack(fill="x", padx=10, pady=5)
        
        self.cred_title = ctk.CTkLabel(self.cred_frame, text="Login", font=ctk.CTkFont(size=12, weight="bold"))
        self.cred_title.pack(padx=10, pady=(6, 4), anchor="w")
        
        self.username_entry = ctk.CTkEntry(self.cred_frame, placeholder_text="NIK / Nippos", height=28)
        self.username_entry.pack(fill="x", padx=10, pady=3)
        
        self.password_entry = ctk.CTkEntry(self.cred_frame, placeholder_text="Kata Sandi NDE", show="*", height=28)
        self.password_entry.pack(fill="x", padx=10, pady=3)
        
        self.show_pass_var = ctk.StringVar(value="off")
        self.show_pass_cb = ctk.CTkCheckBox(self.cred_frame, text="Tampilkan Sandi", font=ctk.CTkFont(size=11), variable=self.show_pass_var, onvalue="on", offvalue="off", command=self.toggle_password_visibility, checkbox_width=16, checkbox_height=16)
        self.show_pass_cb.pack(padx=10, pady=(4, 8), anchor="w")
        
        # 2. Pengaturan Unduhan (Folder Tujuan)
        self.dir_frame = ctk.CTkFrame(self.sidebar_frame)
        self.dir_frame.pack(fill="x", padx=10, pady=5)
        
        self.dir_title = ctk.CTkLabel(self.dir_frame, text="Folder Hasil Unduhan", font=ctk.CTkFont(size=12, weight="bold"))
        self.dir_title.pack(padx=10, pady=(6, 4), anchor="w")
        
        self.dir_sub_frame = ctk.CTkFrame(self.dir_frame, fg_color="transparent")
        self.dir_sub_frame.pack(fill="x", padx=10, pady=2)
        
        self.dir_entry = ctk.CTkEntry(self.dir_sub_frame, placeholder_text="Pilih folder...", height=28)
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.dir_btn = ctk.CTkButton(self.dir_sub_frame, text="...", width=28, height=28, command=self.browse_directory)
        self.dir_btn.pack(side="right")

        # Padding bawah dir_frame
        ctk.CTkLabel(self.dir_frame, text="").pack(pady=2)
        
        # 2.5 Pengaturan Tambahan (Checkbox Menu & Mulai Indeks)
        self.opt_frame = ctk.CTkFrame(self.sidebar_frame)
        self.opt_frame.pack(fill="x", padx=10, pady=5)
        
        self.opt_title = ctk.CTkLabel(self.opt_frame, text="Pilihan Menu & Urutan", font=ctk.CTkFont(size=12, weight="bold"))
        self.opt_title.pack(padx=10, pady=(6, 4), anchor="w")
        
        # Menu Checkboxes (2x2 grid)
        self.menu_cb_frame = ctk.CTkFrame(self.opt_frame, fg_color="transparent")
        self.menu_cb_frame.pack(fill="x", padx=10, pady=2)
        
        self.menu_vars = {}
        self.menu_checkboxes = []
        menus_list = ["Surat Keluar", "Surat Masuk", "Verifikasi", "Disposisi"]
        
        for idx, m_name in enumerate(menus_list):
            var = ctk.BooleanVar(value=True)
            self.menu_vars[m_name] = var
            cb = ctk.CTkCheckBox(self.menu_cb_frame, text=m_name, font=ctk.CTkFont(size=10), variable=var, checkbox_width=14, checkbox_height=14)
            cb.grid(row=idx//2, column=idx%2, padx=5, pady=3, sticky="w")
            self.menu_checkboxes.append(cb)
            
        # Start & End Index Input Range
        self.range_frame = ctk.CTkFrame(self.opt_frame, fg_color="transparent")
        self.range_frame.pack(fill="x", padx=10, pady=(5, 10))
        
        self.range_label = ctk.CTkLabel(self.range_frame, text="Batasan Urutan:", font=ctk.CTkFont(size=11))
        self.range_label.pack(side="left")
        
        self.end_idx_entry = ctk.CTkEntry(self.range_frame, placeholder_text="s/d akhir", width=65, height=24, font=ctk.CTkFont(size=11))
        self.end_idx_entry.pack(side="right")
        
        self.to_label = ctk.CTkLabel(self.range_frame, text=" s/d ", font=ctk.CTkFont(size=11))
        self.to_label.pack(side="right", padx=2)
        
        self.start_idx_entry = ctk.CTkEntry(self.range_frame, placeholder_text="1", width=50, height=24, font=ctk.CTkFont(size=11))
        self.start_idx_entry.pack(side="right")
        
        # 3. Tab Mode Unduhan
        self.tab_frame = ctk.CTkFrame(self.sidebar_frame)
        self.tab_frame.pack(fill="x", padx=10, pady=5)
        
        self.tab_control = ctk.CTkTabview(self.tab_frame, height=95)
        self.tab_control.pack(fill="x", padx=5, pady=2)
        
        self.tab_all = self.tab_control.add("Tarik Semua Data")
        self.tab_excel = self.tab_control.add("Pencarian via Excel")
        
        # Tab 1: Download Semua (Info)
        self.tab_all_label = ctk.CTkLabel(self.tab_all, text="Unduh otomatis seluruh surat & lampiran dari menu utama NDE.", justify="left", wraplength=200, font=ctk.CTkFont(size=10))
        self.tab_all_label.pack(padx=8, pady=5, anchor="w")
        
        # Tab 2: Pencarian Excel
        self.excel_sub_frame = ctk.CTkFrame(self.tab_excel, fg_color="transparent")
        self.excel_sub_frame.pack(fill="x", padx=5, pady=5)
        
        self.excel_entry = ctk.CTkEntry(self.excel_sub_frame, placeholder_text="Pilih file Excel...", height=26, font=ctk.CTkFont(size=11))
        self.excel_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.excel_btn = ctk.CTkButton(self.excel_sub_frame, text="...", width=26, height=26, command=self.browse_excel)
        self.excel_btn.pack(side="right")
        
        # Theme Selector
        self.theme_frame = ctk.CTkFrame(self.sidebar_frame)
        self.theme_frame.pack(fill="x", padx=10, pady=5)
        self.theme_sub_frame = ctk.CTkFrame(self.theme_frame, fg_color="transparent")
        self.theme_sub_frame.pack(fill="x", padx=10, pady=5)
        
        self.theme_label = ctk.CTkLabel(self.theme_sub_frame, text="Tema:", font=ctk.CTkFont(size=11))
        self.theme_label.pack(side="left", padx=(0, 10))
        
        self.theme_optionmenu = ctk.CTkOptionMenu(self.theme_sub_frame, values=["Dark", "Light", "System"], height=24, width=100, font=ctk.CTkFont(size=11), command=self.change_appearance_mode_event)
        self.theme_optionmenu.pack(side="right", fill="x", expand=True)
        
        # Info Box
        self.info_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.info_frame.pack(padx=10, pady=(5, 10))
        self.info_label = ctk.CTkLabel(self.info_frame, text="Dibuat Oleh: AlvaraID", font=ctk.CTkFont(size=9), justify="center")
        self.info_label.pack()

    def create_main_content(self):
        # Main Content Frame (Right side)
        self.main_frame = ctk.CTkFrame(self, corner_radius=0)
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        
        # 1. Progress dan Panel Aksi
        self.action_frame = ctk.CTkFrame(self.main_frame)
        self.action_frame.pack(fill="x", padx=15, pady=15)
        
        self.progress_title = ctk.CTkLabel(self.action_frame, text="Progress", font=ctk.CTkFont(size=14, weight="bold"))
        self.progress_title.pack(padx=15, pady=(15, 5), anchor="w")
        
        self.progress_bar = ctk.CTkProgressBar(self.action_frame)
        self.progress_bar.pack(fill="x", padx=15, pady=(5, 2))
        self.progress_bar.set(0)
        
        # Label info progress: "X / Total (Y%)"
        self.progress_info_label = ctk.CTkLabel(
            self.action_frame,
            text="Belum dimulai",
            font=ctk.CTkFont(size=11),
            text_color=("gray60", "gray50")
        )
        self.progress_info_label.pack(anchor="e", padx=15, pady=(0, 5))
        
        # Buttons container (Mulai & Stop side-by-side)
        self.btn_frame = ctk.CTkFrame(self.action_frame, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=15, pady=(5, 15))
        
        self.start_btn = ctk.CTkButton(self.btn_frame, text="Mulai Unduh", fg_color="#2ecc71", hover_color="#27ae60", font=ctk.CTkFont(weight="bold"), command=self.start_process)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.cancel_btn = ctk.CTkButton(self.btn_frame, text="Batal / Stop", fg_color="#e74c3c", hover_color="#c0392b", state="disabled", font=ctk.CTkFont(weight="bold"), command=self.cancel_process)
        self.cancel_btn.pack(side="right", fill="x", expand=True, padx=(5, 0))
        
        # 2. Log Console
        self.log_frame = ctk.CTkFrame(self.main_frame)
        self.log_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        
        self.log_title = ctk.CTkLabel(self.log_frame, text="Log Console / Aktivitas", font=ctk.CTkFont(size=14, weight="bold"))
        self.log_title.pack(padx=15, pady=(15, 5), anchor="w")
        
        self.log_text = ctk.CTkTextbox(self.log_frame, wrap="word", font=ctk.CTkFont(family="Consolas", size=11))
        self.log_text.pack(fill="both", expand=True, padx=15, pady=15)
        
    def toggle_password_visibility(self):
        if self.show_pass_var.get() == "on":
            self.password_entry.configure(show="")
        else:
            self.password_entry.configure(show="*")
            
    def change_appearance_mode_event(self, new_appearance_mode: str):
        ctk.set_appearance_mode(new_appearance_mode.lower())

    def browse_directory(self):
        directory = filedialog.askdirectory()
        if directory:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, directory)
            self.save_config_data()

    def browse_excel(self):
        file_path = filedialog.askopenfilename(filetypes=[("Excel Files", "*.xlsx;*.xls")])
        if file_path:
            self.excel_entry.delete(0, "end")
            self.excel_entry.insert(0, file_path)

    def write_log(self, message, level="info"):
        self.log_queue.put((message, level))
        log_file = Path(__file__).parent / "app.log"
        try:
            prefix = "[INFO] "
            if level == "success":
                prefix = "[SUCCESS] "
            elif level == "warning":
                prefix = "[WARNING] "
            elif level == "error":
                prefix = "[ERROR] "
            elif level == "debug":
                prefix = "[DEBUG] "
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} {prefix}{message}\n")
        except Exception:
            pass

    def update_progress(self, percent):
        # Memaksa update di main thread
        self.after(0, lambda: self.progress_bar.set(percent))

    def update_progress_info(self, downloaded, total):
        """Update label progress info di main thread."""
        def _update():
            if total > 0:
                pct = min(100, int((downloaded / total) * 100))
                self.progress_info_label.configure(
                    text=f"{downloaded} / {total} surat  ({pct}%)",
                    text_color=("#2ecc71", "#2ecc71") if pct == 100 else ("gray70", "gray60")
                )
            elif total == 0 and downloaded > 0:
                # Total belum diketahui tapi sudah ada yang selesai
                self.progress_info_label.configure(
                    text=f"{downloaded} surat selesai",
                    text_color=("gray70", "gray60")
                )
            else:
                self.progress_info_label.configure(
                    text="Menghitung total surat...",
                    text_color=("gray60", "gray50")
                )
        self.after(0, _update)

    def poll_logs(self):
        try:
            while True:
                msg, level = self.log_queue.get_nowait()
                
                # Masukkan ke text box
                self.log_text.configure(state="normal")
                
                prefix = "[INFO] "
                show_in_console = True
                if level == "success":
                    prefix = "[SUCCESS] "
                elif level == "warning":
                    prefix = "[WARNING] "
                    # Sembunyikan warning pagination generik di console, tapi tetap tulis ke app.log
                    if "Halaman tidak berubah setelah klik Next" in msg or "menghentikan paginasi" in msg:
                        show_in_console = False
                elif level == "error":
                    prefix = "[ERROR] "
                elif level == "debug":
                    prefix = "[DEBUG] "
                    show_in_console = False
                    # Hanya simpan debug ke app.log; jangan tampilkan di console untuk pengguna umum

                if show_in_console:
                    self.log_text.insert("end", f"{prefix}{msg}\n")
                    self.log_text.see("end")
                self.log_text.configure(state="disabled")
                self.log_queue.task_done()
        except queue.Empty:
            pass
        self.after(500, self.poll_logs)

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_config_data(self):
        config = {
            "username": self.username_entry.get().strip(),
            "download_dir": self.dir_entry.get().strip(),
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
        except Exception:
            pass

    def load_saved_data(self):
        if self.config:
            if "username" in self.config:
                self.username_entry.insert(0, self.config["username"])
            if "download_dir" in self.config:
                self.dir_entry.insert(0, self.config["download_dir"])

    def set_gui_state(self, running=True):
        state = "disabled" if running else "normal"
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.username_entry.configure(state=state)
        self.password_entry.configure(state=state)
        self.dir_entry.configure(state=state)
        self.dir_btn.configure(state=state)
        self.excel_entry.configure(state=state)
        self.excel_btn.configure(state=state)
        self.start_idx_entry.configure(state=state)
        self.end_idx_entry.configure(state=state)
        for cb in getattr(self, "menu_checkboxes", []):
            cb.configure(state=state)
        if running:
            self.progress_bar.set(0)
            self.progress_info_label.configure(text="Belum dimulai", text_color=("gray60", "gray50"))

    def start_process(self):
        # Validasi
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        download_dir = self.dir_entry.get().strip()
        headless = True  # Ubah ke False agar browser terlihat (untuk debugging)
        
        current_tab = self.tab_control.get()
        excel_path = None
        
        if not username or not password:
            messagebox.showerror("Eror Validasi", "NIK/Nippos dan Kata Sandi wajib diisi!")
            return
            
        if not download_dir:
            messagebox.showerror("Eror Validasi", "Tentukan folder tujuan penyimpanan hasil unduhan!")
            return
            
        if current_tab == "Pencarian via Excel":
            excel_path = self.excel_entry.get().strip()
            if not excel_path:
                messagebox.showerror("Eror Validasi", "Pilih berkas Excel terlebih dahulu!")
                return
            if not os.path.exists(excel_path):
                messagebox.showerror("Eror Validasi", "Berkas Excel yang dipilih tidak valid atau tidak ditemukan!")
                return

        # Ambil menu yang dicentang
        selected_menus = [menu for menu, var in self.menu_vars.items() if var.get()]
        if not selected_menus:
            messagebox.showerror("Eror Validasi", "Pilih minimal satu menu NDE yang akan diproses!")
            return
            
        # Ambil start index
        start_idx_str = self.start_idx_entry.get().strip()
        start_index = 1
        if start_idx_str:
            if start_idx_str.isdigit():
                start_index = int(start_idx_str)
                if start_index < 1:
                    start_index = 1
            else:
                messagebox.showerror("Eror Validasi", "Mulai Urutan Ke harus berupa angka bulat positif!")
                return
                
        # Ambil end index
        end_idx_str = self.end_idx_entry.get().strip()
        end_index = None
        if end_idx_str:
            if end_idx_str.isdigit():
                end_index = int(end_idx_str)
                if end_index < start_index:
                    messagebox.showerror("Eror Validasi", "Sampai Urutan Ke tidak boleh lebih kecil dari Mulai Urutan Ke!")
                    return
            else:
                messagebox.showerror("Eror Validasi", "Sampai Urutan Ke harus berupa angka bulat positif!")
                return

        # Simpan config
        self.save_config_data()
        
        # Bersihkan log console
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        
        # Hapus log file lama
        log_file = Path(__file__).parent / "app.log"
        if log_file.exists():
            try:
                log_file.unlink()
            except Exception:
                pass
        
        # Set state running
        self.set_gui_state(True)
        self.stop_event.clear()
        self.progress_info_label.configure(text="Menghitung total surat...", text_color=("gray60", "gray50"))
        
        # Setup dan jalankan thread worker
        self.downloader = NDEDownloader(
            username=username,
            password=password,
            download_dir=download_dir,
            headless=headless,
            excel_path=excel_path,
            log_callback=self.write_log,
            progress_callback=self.update_progress,
            total_callback=self.update_progress_info,
            stop_event=self.stop_event,
            selected_menus=selected_menus,
            start_index=start_index,
            end_index=end_index
        )
        
        self.running_thread = threading.Thread(target=self.run_downloader_thread, daemon=True)
        self.running_thread.start()

    def run_downloader_thread(self):
        try:
            self.downloader.run()
        except Exception as e:
            self.write_log(f"Error fatal di thread: {str(e)}", "error")
        finally:
            self.after(0, self.show_completion_message)
            self.after(0, lambda: self.set_gui_state(False))

    def show_completion_message(self):
        if not self.downloader:
            return
            
        status = getattr(self.downloader, "status", "idle")
        if status == "login_failed":
            messagebox.showerror("Gagal Masuk", "Login gagal! Periksa kembali NIK/Nippos dan kata sandi NDE Anda.")
        elif status == "error":
            messagebox.showerror("Kesalahan", "Terjadi kesalahan selama pengunduhan. Silakan periksa log console.")
        elif status == "cancelled":
            messagebox.showwarning("Dibatalkan", "Proses pengunduhan telah dibatalkan oleh pengguna.")
        elif status == "success":
            messagebox.showinfo("Sukses", "Unduhan selesai! Seluruh dokumen berhasil diproses.")

    def cancel_process(self):
        if self.downloader:
            self.write_log("Mengirim sinyal pembatalan ke pengunduh...", "warning")
            self.stop_event.set()
            self.cancel_btn.configure(state="disabled")

if __name__ == "__main__":
    app = NDEDownloaderGUI()
    app.mainloop()
