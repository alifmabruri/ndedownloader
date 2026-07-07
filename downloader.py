import os
import re
import sys
import time
import traceback
from pathlib import Path
import pandas as pd
from playwright.sync_api import sync_playwright

class NDEDownloader:
    def __init__(self, username, password, download_dir, headless=False, excel_path=None, log_callback=None, progress_callback=None, total_callback=None, stop_event=None, selected_menus=None, start_index=1, end_index=None):
        self.username = username
        self.password = password
        self.download_dir = Path(download_dir)
        self.headless = headless
        self.excel_path = excel_path
        self.log_callback = log_callback if log_callback else print
        self.progress_callback = progress_callback if progress_callback else (lambda p: None)
        self.total_callback = total_callback if total_callback else (lambda downloaded, total: None)
        self.stop_event = stop_event
        self.selected_menus = selected_menus if selected_menus else ["Surat Keluar", "Surat Masuk", "Verifikasi", "Disposisi"]
        self.start_index = start_index
        self.end_index = end_index
        self.status = "idle"
        # Tracking progress unduhan
        self._total_nde = 0        # Total surat yang akan diproses
        self._downloaded = 0       # Jumlah surat yang sudah berhasil diunduh
        
    def log(self, message, level="info"):
        self.log_callback(message, level)
        
    def check_stop(self):
        if self.stop_event and self.stop_event.is_set():
            self.log("Proses dibatalkan oleh pengguna.", "warning")
            raise InterruptedError("Proses dibatalkan oleh pengguna.")

    def sanitize_filename(self, filename: str) -> str:
        # Hapus karakter ilegal untuk nama file/folder di Windows: \ / : * ? " < > |
        cleaned = re.sub(r'[\\/*?:"<>|]', "", filename)
        # Hapus spasi berlebih dan baris baru
        cleaned = re.sub(r'\s+', " ", cleaned).strip()
        return cleaned if cleaned else "Tanpa_Nama"

    def _safe_click(self, page, locator, timeout=5000):
        """Click a locator safely: try normal click, then JS click, force click, and overlay removal."""
        try:
            locator.wait_for(state="attached", timeout=timeout)
            locator.scroll_into_view_if_needed(timeout=timeout)
            locator.click(timeout=timeout)
            return True
        except Exception as e:
            self.log(f"Klik biasa gagal ({str(e)[:120]}), mencoba metode alternatif...", "debug")

        # Coba JS click via locator.evaluate
        try:
            locator.evaluate("el => { el.scrollIntoView({block:'center', inline:'center'}); el.click(); }")
            return True
        except Exception:
            pass

        # Coba force click jika tersedia
        try:
            locator.click(force=True, timeout=timeout)
            return True
        except Exception:
            pass

        # Coba sembunyikan overlay umum yang mungkin intercept pointer events, lalu klik lagi
        try:
            page.evaluate("""
                () => {
                    const selectors = [
                        'div[data-state="open"][aria-hidden="true"]',
                        'div[class*="overlay"]',
                        'div[class*="backdrop"]',
                        'div[class*="bg-black/80"]',
                        '[data-radix-portal]',
                        '[role="presentation"]',
                        '.modal-backdrop',
                        '.fixed',
                    ];
                    let hidden = 0;
                    const candidates = Array.from(new Set(selectors.flatMap(sel => Array.from(document.querySelectorAll(sel)))));
                    for (const el of candidates) {
                        try {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                const style = window.getComputedStyle(el);
                                if ((style.position === 'fixed' || style.position === 'absolute') && style.pointerEvents !== 'none') {
                                    el.__copilot_orig_display = el.style.display || '';
                                    el.__copilot_orig_pointer = el.style.pointerEvents || '';
                                    el.style.display = 'none';
                                    el.style.pointerEvents = 'none';
                                    hidden += 1;
                                }
                            }
                        } catch (err) {
                            continue;
                        }
                    }
                    return hidden;
                }
            """)
            try:
                locator.evaluate("el => { el.scrollIntoView({block:'center', inline:'center'}); el.click(); }")
                return True
            except Exception:
                pass
        except Exception:
            pass

        return False

    def find_menu_button(self, page, menu):
        candidates = []
        try:
            candidates.append(page.get_by_role("button", name=menu))
        except Exception:
            pass
        try:
            candidates.append(page.get_by_role("button", name=menu, exact=False))
        except Exception:
            pass
        try:
            candidates.append(page.get_by_text(menu, exact=True))
        except Exception:
            pass
        try:
            candidates.append(page.get_by_text(menu))
        except Exception:
            pass
        selectors = [
            f"button:has-text('{menu}')",
            f"[role='button']:has-text('{menu}')",
            f"a:has-text('{menu}')",
            f"*:has-text('{menu}')",
            f"text={menu}"
        ]
        for sel in selectors:
            try:
                candidates.append(page.locator(sel))
            except Exception:
                pass

        for candidate in candidates:
            try:
                if candidate is None or candidate.count() == 0:
                    continue
                loc = candidate.first
                if loc.is_visible():
                    return loc
            except Exception:
                continue

        # Last fallback: query via JS and find visible elements containing the menu text.
        try:
            button_handle = page.evaluate_handle(
                """(menuText) => {
                    const candidates = Array.from(document.querySelectorAll('button, [role=button], a, div, span'));
                    for (const el of candidates) {
                        const text = el.innerText || el.textContent || '';
                        if (text.trim() === menuText.trim() || text.trim().includes(menuText.trim())) {
                            if (el.offsetParent !== null) {
                                return el;
                            }
                        }
                    }
                    return null;
                }""",
                menu
            )
            if button_handle and page.evaluate("el => !!el", button_handle):
                return page.locator("xpath=.", has=button_handle).first
        except Exception:
            pass

        self.log(f"Tidak dapat menemukan tombol menu '{menu}' yang terlihat.", "debug")
        return None

    def run(self):
        self.log("Memulai proses unduh NDE...")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        with sync_playwright() as p:
            self.log("Membuka browser...")
            # Coba gunakan Chrome/Edge yang sudah terinstall di sistem
            browser = None
            for channel in ["chrome", "msedge", "chromium"]:
                try:
                    if channel == "chromium":
                        browser = p.chromium.launch(headless=self.headless)
                    else:
                        browser = p.chromium.launch(headless=self.headless, channel=channel)
                    self.log(f"Menggunakan browser: {channel}", "debug")
                    break
                except Exception:
                    continue
            if browser is None:
                raise RuntimeError("Tidak dapat membuka browser. Pastikan Google Chrome atau Microsoft Edge terinstall.")
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                accept_downloads=True
            )
            # Mencegah tab cetak otomatis menutup dirinya sendiri
            context.add_init_script("""
                window.close = function() { console.log('Prevented window.close()'); };
                window.print = function() { console.log('Prevented window.print()'); };
            """)
            page = context.new_page()
            
            try:
                # 1. Login ke NDE
                self.log("Navigasi ke halaman login NDE...")
                page.goto("https://nde.posindonesia.co.id/auth/login?callbackUrl=%2Fdashboard", wait_until="domcontentloaded", timeout=60000)
                
                # Tunggu halaman benar-benar siap
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass  # Lanjutkan meskipun networkidle timeout
                
                self.log("Menunggu form login...")
                
                # Coba berbagai selector untuk input username
                username_input = None
                username_selectors = [
                    'input[placeholder*="Masukan Nippos atau NIK"]',
                    'input[placeholder*="Nippos"]',
                    'input[placeholder*="NIK"]',
                    'input[placeholder*="nippos"]',
                    'input[placeholder*="nik"]',
                    'input[type="text"]',
                    'input[name="username"]',
                    'input[name="nippos"]',
                    'input[id*="username"]',
                    'input[id*="nippos"]',
                ]
                for sel in username_selectors:
                    try:
                        page.wait_for_selector(sel, timeout=8000)
                        username_input = page.locator(sel).first
                        self.log(f"Form login berhasil ditemukan.", "debug")
                        break
                    except Exception:
                        continue
                
                if username_input is None:
                    self.log("Form login tidak ditemukan. Halaman mungkin tidak dapat diakses.", "error")
                    self.status = "login_failed"
                    raise RuntimeError("Form login tidak ditemukan setelah mencoba semua selector.")
                
                self.check_stop()
                
                self.log("Mengisi NIK/Nippos...")
                username_input.click()
                username_input.fill(self.username)
                username_input.press("Tab")
                
                self.log("Mengisi kata sandi...")
                # Coba berbagai selector untuk input password
                password_input = None
                password_selectors = [
                    'input[placeholder*="Masukan kata sandi"]',
                    'input[placeholder*="kata sandi"]',
                    'input[placeholder*="password"]',
                    'input[placeholder*="Password"]',
                    'input[type="password"]',
                    'input[name="password"]',
                ]
                for sel in password_selectors:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            password_input = loc.first
                            break
                    except Exception:
                        continue
                
                if password_input:
                    password_input.fill(self.password)
                else:
                    # Fallback: input kedua yang terlihat
                    all_inputs = page.locator("input").all()
                    for inp in all_inputs:
                        try:
                            if inp.get_attribute("type") in ("password", None, "text") and inp.is_visible():
                                if inp != username_input:
                                    inp.fill(self.password)
                                    password_input = inp
                                    break
                        except Exception:
                            pass
                
                self.check_stop()
                
                self.log("Mengklik tombol masuk...")
                # Coba berbagai nama tombol login
                login_clicked = False
                for btn_name in ["Masuk", "Login", "Sign In", "Submit"]:
                    try:
                        btn = page.get_by_role("button", name=btn_name)
                        if btn.count() > 0:
                            btn.first.click()
                            login_clicked = True
                            break
                    except Exception:
                        pass
                if not login_clicked:
                    page.locator("button[type='submit']").first.click()
                
                # Menunggu login selesai dan dashboard terbuka
                self.log("Menunggu dashboard NDE dimuat...")
                try:
                    page.wait_for_url("**/dashboard", timeout=60000)
                    self.log("Login berhasil!", "success")
                except Exception as login_err:
                    self.log("Login gagal! Periksa kembali NIK dan password Anda.", "error")
                    self.status = "login_failed"
                    raise login_err
                
                # Menutup popups atau panduan selamat datang jika ada
                page.wait_for_timeout(3000)
                try:
                    # Gunakan timeout pendek agar tidak macet jika popup tidak muncul
                    page.get_by_role("button", name="Selanjutnya").click(timeout=3000)
                    self.log("Menutup popup panduan pertama...")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                    
                try:
                    page.get_by_role("button", name="Tutup").click(timeout=3000)
                    self.log("Menutup popup pengumuman...")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                
                # 2. Proses Berdasarkan Mode
                if self.excel_path:
                    self.run_excel_mode(page)
                else:
                    # Hitung total surat terlebih dahulu sebelum mulai download
                    self._count_total_items(page)
                    self.run_all_mode(page)
                    
                self.log("Proses selesai!", "success")
                self.progress_callback(1.0)
                self.status = "success"
                
            except InterruptedError:
                self.status = "cancelled"
            except Exception as e:
                if self.status != "login_failed":
                    self.status = "error"
                self.log(f"Terjadi kesalahan: {str(e)}", "error")
                self.log(traceback.format_exc(), "debug")
            finally:
                self.log("Menutup browser...")
                context.close()
                browser.close()

    def _count_total_items(self, page):
        """Menghitung total surat di semua menu sebelum proses download dimulai."""
        menus = self.selected_menus
        list_selector = ".w-full.flex.justify-between"
        total = 0

        self.log("Menghitung total surat yang akan diunduh, mohon tunggu sebentar...")

        for menu in menus:
            self.check_stop()
            try:
                # Navigasi ke menu
                btn = self.find_menu_button(page, menu)
                if btn is None or not self._safe_click(page, btn, timeout=5000):
                    raise Exception("Gagal klik menu via safe_click")
                page.wait_for_timeout(2000)
                self.ensure_open_state(page)

                # Klik filter paling kiri jika ada
                try:
                    filter_locator = page.locator("div.flex.gap-2.mt-2.mb-2 button, div.flex.gap-2.mt-2.mb-2 a")
                    if filter_locator.count() > 0:
                        filter_btn = None
                        for i in range(filter_locator.count()):
                            candidate = filter_locator.nth(i)
                            if candidate.is_visible():
                                filter_btn = candidate
                                break
                        if filter_btn is not None:
                            if not self._safe_click(page, filter_btn, timeout=3000):
                                self.log("Filter button tidak dapat diklik meskipun terlihat.", "debug")
                            else:
                                page.wait_for_timeout(1500)
                except Exception:
                    pass

                # Hitung item per halaman
                list_selector = ".w-full.flex.justify-between"
                page_num = 1
                menu_total = 0
                while True:
                    try:
                        page.wait_for_selector(list_selector, timeout=8000)
                    except Exception:
                        break

                    items = page.locator(list_selector)
                    count = items.count()
                    if count == 0:
                        break
                    menu_total += count
                    prev_snapshot = self._snapshot_page_state(page, list_selector)

                    # Coba klik tombol Next menggunakan semua metode yang tersedia
                    nav_ok = False

                    # Metode 1: Playwright CSS locator (termasuk SVG selector yang terbukti bekerja)
                    next_btn = self.get_next_page_button(page)
                    if next_btn is not None and next_btn != "JS_CLICK":
                        if self._safe_click(page, next_btn, timeout=3000):
                            nav_ok = True
                        else:
                            self.log("Next page button ditemukan tetapi klik gagal.", "debug")

                    # Metode 2: JS click (fallback)
                    if not nav_ok:
                        nav_ok = self._click_next_page_js(page)

                    if not nav_ok:
                        break

                    page.wait_for_timeout(1500)
                    new_snapshot = self._snapshot_page_state(page, list_selector)
                    if prev_snapshot is not None and new_snapshot == prev_snapshot:
                        self.log("Halaman tidak berubah setelah klik Next — menghentikan paginasi.", "warning")
                        break

                    page_num += 1

                self.log(f"Menu {menu}: {menu_total} surat ditemukan.", "debug")
                total += menu_total
            except Exception as e:
                self.log(f"Tidak dapat menghitung surat di menu {menu}: {str(e)}", "debug")

        # Terapkan filter start/end jika ada
        if self.end_index is not None:
            effective_total = min(total, self.end_index) - self.start_index + 1
        else:
            effective_total = max(0, total - self.start_index + 1)

        self._total_nde = max(0, effective_total)
        self.log(f"Total surat yang akan diunduh: {self._total_nde} surat.", "success")
        self.total_callback(self._downloaded, self._total_nde)

    def run_all_mode(self, page):
        menus = self.selected_menus
        total_menus = len(menus)
        
        for idx, menu in enumerate(menus):
            self.check_stop()
            self.log(f"--- Menu [{idx+1}/{total_menus}]: {menu} ---")
            
            # Navigasi ke menu
            try:
                self.log(f"Membuka menu {menu}...")
                btn = self.find_menu_button(page, menu)
                if btn is None or not self._safe_click(page, btn, timeout=5000):
                    raise Exception("Gagal klik menu via safe_click")
                page.wait_for_timeout(2000)
            except Exception as e:
                self.log(f"Gagal membuka menu {menu}: {str(e)}", "error")
                continue
                
            # Klik filter paling kiri jika ada untuk mereset/memperluas pencarian
            try:
                filter_locator = page.locator("div.flex.gap-2.mt-2.mb-2 button, div.flex.gap-2.mt-2.mb-2 a")
                if filter_locator.count() > 0:
                    filter_btn = None
                    for i in range(filter_locator.count()):
                        candidate = filter_locator.nth(i)
                        if candidate.is_visible():
                            filter_btn = candidate
                            break
                    if filter_btn is not None:
                        if self._safe_click(page, filter_btn, timeout=3000):
                            self.log("Mengatur filter tampilan surat...", "debug")
                            page.wait_for_timeout(1000)
                        else:
                            self.log("Filter button ditemukan tetapi gagal diklik.", "debug")
            except Exception:
                pass
            
            # Memastikan semua panel tertutup terbuka sebelum membaca daftar
            self.ensure_open_state(page)
            # Memproses semua item di daftar
            self.process_list_items(page, menu)

    def run_excel_mode(self, page):
        self.log(f"Membaca file Excel dari: {self.excel_path}")
        if not os.path.exists(self.excel_path):
            self.log("File Excel tidak ditemukan!", "error")
            return
            
        try:
            df = pd.read_excel(self.excel_path)
            if df.empty:
                self.log("File Excel kosong!", "error")
                return
                
            # Mencari kolom nomor surat
            doc_col = None
            for col in df.columns:
                col_lower = str(col).lower()
                if "nomor" in col_lower or "no" in col_lower or "surat" in col_lower or "dokumen" in col_lower:
                    doc_col = col
                    break
            
            if doc_col is None:
                doc_col = df.columns[0]
                self.log(f"Kolom nomor surat tidak terdeteksi secara otomatis. Menggunakan kolom pertama: '{doc_col}'", "warning")
            else:
                self.log(f"Menggunakan kolom: '{doc_col}' untuk pencarian nomor surat.")
                
            # Ambil semua nomor surat unik dan bersihkan
            doc_numbers = df[doc_col].dropna().astype(str).str.strip().unique().tolist()
            total_docs = len(doc_numbers)
            
            start_idx = self.start_index - 1
            if start_idx < 0:
                start_idx = 0
                
            end_idx = self.end_index if self.end_index is not None else total_docs
            if end_idx > total_docs:
                end_idx = total_docs
                
            if start_idx >= total_docs or start_idx >= end_idx:
                self.log(f"Batasan indeks mulai ({self.start_index}) s/d ({end_idx}) tidak valid untuk total {total_docs} data di Excel. Dilewati.", "warning")
                return
                
            doc_numbers = doc_numbers[start_idx:end_idx]
            self.log(f"Mulai memproses {len(doc_numbers)} nomor surat dari urutan ke-{self.start_index} s/d {end_idx} di Excel (Sisa dari {total_docs}).")
            
            for idx, doc_num in enumerate(doc_numbers):
                self.check_stop()
                self.progress_callback(idx / len(doc_numbers))
                original_idx = start_idx + idx
                self.log(f"[{original_idx+1}/{total_docs}] Mencari surat nomor: {doc_num}")
                
                success = self.search_and_download(page, doc_num)
                if success:
                    self.log(f"Berhasil menemukan dan mengunduh surat: {doc_num}", "success")
                else:
                    self.log(f"Surat tidak ditemukan di menu mana pun: {doc_num}", "warning")
                    
        except Exception as e:
            self.log(f"Gagal memproses Excel: {str(e)}", "error")
            self.log(traceback.format_exc(), "debug")

    def search_and_download(self, page, doc_number):
        menus = ["Surat Keluar", "Surat Masuk", "Verifikasi", "Disposisi"]
        found = False
        
        for menu in menus:
            self.check_stop()
            self.log(f"Mencari '{doc_number}' di menu {menu}...")
            
            # Navigasi ke menu
            try:
                btn = self.find_menu_button(page, menu)
                if btn is None or not self._safe_click(page, btn, timeout=5000):
                    raise Exception("Gagal klik menu via safe_click")
                page.wait_for_timeout(2000)
                self.ensure_open_state(page)

                # Klik filter paling kiri jika ada untuk mereset/memperluas pencarian
                try:
                    filter_locator = page.locator("div.flex.gap-2.mt-2.mb-2 button, div.flex.gap-2.mt-2.mb-2 a")
                    if filter_locator.count() > 0:
                        if not self._safe_click(page, filter_locator.first, timeout=3000):
                            filter_locator.first.click(timeout=3000)
                        self.log("Mengatur filter tampilan surat...", "debug")
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
            except Exception:
                continue
                
            # Mencari kotak pencarian
            search_input = None
            selectors = [
                "input[placeholder*='Cari']",
                "input[placeholder*='Search']",
                "input[placeholder*='nomor']",
                "input[type='text']",
                "input"
            ]
            for sel in selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        # Tunggu agar input siap
                        loc.first.wait_for(state="visible", timeout=2000)
                        search_input = loc.first
                        break
                except Exception:
                    pass
                    
            if not search_input:
                self.log(f"Kotak pencarian tidak tersedia di menu {menu}.", "debug")
                continue
                
            # Masukkan nomor surat dan cari
            try:
                search_input.click()
                search_input.press("ControlOrMeta+a")
                search_input.press("Backspace")
                search_input.fill(doc_number)
                
                # Cari tombol pencarian (icon kaca pembesar) dan klik, jika tidak ada tekan Enter
                search_btn = page.locator("button:has(svg.lucide-search), button:has(.lucide-search)").first
                if search_btn.count() > 0:
                    search_btn.click()
                else:
                    search_input.press("Enter")
                    
                page.wait_for_timeout(3500)  # Menunggu hasil pencarian memuat
            except Exception as e:
                self.log(f"Gagal melakukan pencarian di menu {menu}: {str(e)}", "debug")
                continue
                
            # Periksa apakah ada hasil
            try:
                # List items selector
                items = page.locator(".w-full.flex.justify-between")
                count = items.count()
                
                # Fallback selector jika kelas di atas tidak ditemukan/berubah
                if count == 0:
                    items = page.locator(".w-full.bg-white > .w-full.flex.justify-between")
                    count = items.count()
                    
                if count > 0:
                    self.log(f"Surat '{doc_number}' ditemukan di menu {menu}! Memproses...", "success")
                    success = self.process_single_item(page, items.first, menu, search_query=doc_number)
                    if success:
                        found = True
                        break
            except Exception as e:
                self.log(f"Tidak dapat memeriksa hasil pencarian di menu {menu}: {str(e)}", "debug")
                
        return found

    def dump_pagination_debug(self, page):
        """Tulis HTML semua elemen pagination ke file debug untuk analisis."""
        debug_file = Path(__file__).parent / "debug_pagination.html"
        try:
            # Ambil seluruh HTML halaman
            full_html = page.content()
            # Ambil semua teks tombol yang terlihat
            all_buttons = page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a[href]'));
                return btns.map(b => ({
                    tag: b.tagName,
                    text: b.innerText.trim(),
                    class: b.className,
                    disabled: b.disabled || b.getAttribute('disabled') !== null,
                    ariaLabel: b.getAttribute('aria-label') || '',
                    ariaDisabled: b.getAttribute('aria-disabled') || '',
                    visible: b.offsetParent !== null
                }));
            }""")
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write("=== BUTTONS & LINKS ===\n")
                for b in all_buttons:
                    f.write(str(b) + "\n")
                f.write("\n\n=== FULL HTML ===\n")
                f.write(full_html)
            self.log(f"[DEBUG] HTML pagination disimpan ke: {debug_file.name} — kirimkan file ini untuk analisis selector.", "warning")
        except Exception as e:
            self.log(f"[DEBUG] Gagal dump pagination: {str(e)}", "debug")

    def _click_next_page_js(self, page) -> bool:
        """Klik tombol halaman berikutnya via JavaScript. Return True jika berhasil ditemukan & diklik."""
        try:
            result = page.evaluate("""
                () => {
                    const svgs = document.querySelectorAll('svg');
                    for (const svg of svgs) {
                        const cls = svg.getAttribute('class') || '';
                        if (cls.includes('chevron-right') || cls.includes('ChevronRight')) {
                            const btn = svg.closest('button') || svg.closest('a');
                            if (btn && !btn.disabled
                                && btn.getAttribute('aria-disabled') !== 'true'
                                && !(btn.className || '').includes('disabled')) {
                                btn.click();
                                return {ok: true};
                            }
                        }
                    }
                    // Fallback: cari tombol pagination terakhir yang tidak disabled
                    const paginationBtns = document.querySelectorAll(
                        '[class*="pagination"] button, [class*="page"] button'
                    );
                    if (paginationBtns.length > 0) {
                        const last = paginationBtns[paginationBtns.length - 1];
                        if (!last.disabled && last.getAttribute('aria-disabled') !== 'true'
                            && !(last.className || '').includes('disabled')) {
                            last.click();
                            return {ok: true};
                        }
                    }
                    return {ok: false};
                }
            """)
            if isinstance(result, dict):
                return result.get('ok', False)
            return bool(result)
        except Exception:
            return False

    def _snapshot_page_state(self, page, list_selector):
        try:
            items = page.locator(list_selector)
            count = items.count()
            texts = []
            for i in range(min(3, count)):
                try:
                    texts.append(items.nth(i).inner_text().strip())
                except Exception:
                    texts.append("")
            pagination_info = page.evaluate("""
                () => {
                    const nodes = document.querySelectorAll('[aria-current="page"], .page-item.active, .active, [class*=pagination] span, [class*=page-info], [class*=total]');
                    return Array.from(nodes).map(n => n.innerText.trim()).filter(t => t).join('|');
                }
            """)
            return (count, tuple(texts), pagination_info)
        except Exception:
            return None

    def get_next_page_button(self, page):
        """Cari tombol navigasi halaman berikutnya. Kembalikan locator jika ada & bisa diklik, atau None."""
        next_selectors = [
            # --- Teks eksplisit ---
            "button:has-text('Next')",
            "button:has-text('Berikutnya')",
            "button:has-text('Selanjutnya')",
            "a:has-text('Next')",
            "a:has-text('Berikutnya')",
            # --- Karakter panah / simbol ---
            "button:has-text('›')",
            "button:has-text('»')",
            "button:has-text('>')",
            "a:has-text('›')",
            "a:has-text('»')",
            # --- Lucide icon ---
            "button:has(.lucide-chevron-right)",
            "button:has(svg.lucide-chevron-right)",
            "button:has([data-lucide='chevron-right'])",
            "button:has(svg[class*='chevron-right'])",
            "button:has(svg[class*='ChevronRight'])",
            # SVG child selector (original — terbukti bekerja di Playwright)
            "button svg[class*='chevron-right']",
            "button svg[class*='ChevronRight']",
            # --- Aria label ---
            "button[aria-label='Next page']",
            "button[aria-label='Next']",
            "button[aria-label='next']",
            "button[aria-label='Halaman berikutnya']",
            "[aria-label='Next page']",
            "[aria-label='next page']",
            # --- Bootstrap / umum ---
            "li.page-item:not(.disabled) a.page-link[aria-label*='Next']",
            "li:not(.disabled) > a[rel='next']",
            # --- Navigasi pagination generic ---
            "nav button:last-of-type",
            "[class*='pagination'] button:last-child",
            "[class*='pagination'] [class*='next']",
            "[class*='page-next']",
            "[class*='next-page']",
        ]

        for sel in next_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    for i in range(loc.count()):
                        btn = loc.nth(i)
                        if not btn.is_visible():
                            continue
                        if btn.get_attribute("aria-controls") or btn.get_attribute("data-state"):
                            continue
                        disabled_attr = btn.get_attribute("disabled")
                        aria_disabled = btn.get_attribute("aria-disabled")
                        class_attr = btn.get_attribute("class") or ""
                        if disabled_attr is not None:
                            continue
                        if aria_disabled in ("true", "1"):
                            continue
                        class_tokens = class_attr.lower().split()
                        if "disabled" in class_tokens:
                            continue
                        self.log("Tombol halaman berikutnya ditemukan.", "debug")
                        return btn
            except Exception:
                pass

        # Fallback via JavaScript: deteksi tombol next langsung dari DOM
        try:
            js_found = page.evaluate("""() => {
                const svgs = document.querySelectorAll('svg');
                for (const svg of svgs) {
                    const cls = svg.getAttribute('class') || '';
                    if (cls.includes('chevron-right') || cls.includes('ChevronRight')) {
                        const btn = svg.closest('button') || svg.closest('a');
                        if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true'
                            && !(btn.className || '').includes('disabled')) {
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if js_found:
                self.log("Tombol halaman berikutnya ditemukan (deteksi JS).", "debug")
                # Kembalikan sentinel khusus yang menandakan "gunakan JS click"
                return "JS_CLICK"
        except Exception:
            pass

        return None

    def ensure_open_state(self, page):
        """Ensure all collapsible sections are opened if currently closed."""
        try:
            locators = page.locator("button[aria-expanded='false'], button[data-state='closed'][aria-controls], button[data-state='closed'][aria-expanded='false']")
            count = locators.count()
            if count > 0:
                self.log(f"Menemukan {count} section tertutup, membuka semuanya...", "debug")
                for i in range(count):
                    try:
                        btn = locators.nth(i)
                        if not btn.is_visible():
                            continue
                        try:
                            btn.scroll_into_view_if_needed(timeout=2000)
                        except Exception:
                            pass
                        if self._safe_click(page, btn, timeout=3000):
                            page.wait_for_timeout(300)
                            continue
                        try:
                            btn.evaluate("el => { el.scrollIntoView({block:'center', inline:'center'}); el.click(); }")
                            page.wait_for_timeout(300)
                        except Exception:
                            pass
                    except Exception:
                        pass
                self.log("Semua section tertutup telah dibuka.", "debug")
        except Exception as e:
            self.log(f"Gagal memastikan data-state open: {str(e)}", "debug")

    def process_list_items(self, page, menu_name):
        # Tunggu hingga list memuat
        list_selector = ".w-full.flex.justify-between"

        try:
            page.wait_for_selector(list_selector, timeout=12000)
        except Exception:
            self.log(f"Tidak ada daftar surat terdeteksi di menu {menu_name} (atau daftar kosong).", "info")
            return

        # --- Pagination State ---
        global_index = 0          # Indeks surat secara global (lintas halaman)
        page_number = 1
        # Estimasi total dari halaman pertama (akan diperbarui jika ada pagination)
        estimated_total = None

        while True:
            self.check_stop()

            # Hitung item di halaman saat ini
            items = page.locator(list_selector)
            count_on_page = items.count()

            if count_on_page == 0:
                self.log(f"Tidak ada surat di halaman {page_number} menu {menu_name}.", "info")
                break

            # Coba baca total dari UI pagination (jika ada)
            if estimated_total is None:
                try:
                    total_text = page.evaluate("""() => {
                        const els = document.querySelectorAll('[class*=pagination] span, [class*=page-info], [class*=total]');
                        for (const el of els) {
                            const t = el.innerText.trim();
                            if (/\\d/.test(t)) return t;
                        }
                        return null;
                    }""")
                    if total_text:
                        m = re.search(r'(\d+)', total_text)
                        if m:
                            estimated_total = int(m.group(1))
                except Exception:
                    pass

            self.log(f"Halaman {page_number}: {count_on_page} surat ditemukan di menu {menu_name}.")
            prev_snapshot = self._snapshot_page_state(page, list_selector)

            for i in range(count_on_page):
                self.check_stop()

                # Hitung indeks global (1-based) untuk filter start/end
                current_global = global_index + 1

                # Terapkan filter start_index
                if current_global < self.start_index:
                    global_index += 1
                    continue

                # Terapkan filter end_index
                if self.end_index is not None and current_global > self.end_index:
                    self.log(f"Mencapai batas akhir yang ditentukan (surat ke-{self.end_index}). Proses dihentikan.", "info")
                    return

                # Tentukan total untuk display progress
                total_display = self._total_nde if self._total_nde > 0 else None
                if total_display is None and estimated_total is not None:
                    total_display = estimated_total
                elif total_display is None and self.end_index is not None:
                    total_display = self.end_index - self.start_index + 1

                if total_display:
                    pct = min(100, int((self._downloaded / total_display) * 100))
                    self.log(f"Memproses surat ke-{current_global} (hal. {page_number}, baris {i+1}/{count_on_page}) — {self._downloaded}/{total_display} selesai ({pct}%)...")
                else:
                    self.log(f"Memproses surat ke-{current_global} (hal. {page_number}, baris {i+1}/{count_on_page}) — {self._downloaded} selesai...")

                # Relocate item setelah kembali dari detail
                items = page.locator(list_selector)
                if i >= items.count():
                    self.log("Daftar surat berubah, melanjutkan ke halaman berikutnya.", "warning")
                    break

                item = items.nth(i)
                try:
                    result = self.process_single_item(page, item, menu_name)
                    if result:
                        self._downloaded += 1
                        if total_display:
                            pct = min(100, int((self._downloaded / total_display) * 100))
                            self.progress_callback(self._downloaded / total_display)
                            self.total_callback(self._downloaded, total_display)
                            self.log(f"Surat ke-{current_global} selesai — {self._downloaded}/{total_display} ({pct}%) berhasil diunduh.", "success")
                        else:
                            self.total_callback(self._downloaded, 0)
                            self.log(f"Surat ke-{current_global} selesai — total {self._downloaded} surat berhasil diunduh.", "success")
                except Exception as e:
                    self.log(f"Terjadi kesalahan saat memproses surat ke-{current_global}: {str(e)}", "error")
                    self.log(traceback.format_exc(), "debug")

                    # Coba paksa tutup jika stuck di halaman detail
                    for btn_name in ["Close", "Tutup"]:
                        try:
                            page.get_by_role("button", name=btn_name).click(timeout=2000)
                            page.wait_for_timeout(1000)
                        except Exception:
                            pass

                global_index += 1

            # Setelah semua item di halaman ini selesai, cek apakah ada halaman berikutnya
            next_btn = self.get_next_page_button(page)
            if next_btn is None:
                # Jika baru halaman 1 dan ada 10 item (kemungkinan masih ada halaman berikutnya),
                # dump HTML untuk analisis selector
                if page_number == 1 and count_on_page >= 10:
                    self.log(f"Halaman berikutnya tidak dapat diakses di menu {menu_name} meski ada {count_on_page} item — mencoba menyimpan informasi debug.", "warning")
                    self.dump_pagination_debug(page)
                else:
                    self.log(f"Semua halaman di menu {menu_name} telah selesai diproses. Total {self._downloaded} surat berhasil diunduh.", "info")
                break

            # Navigasi ke halaman berikutnya
            page_number += 1
            self.log(f"Melanjutkan ke halaman {page_number}...")

            # Beri waktu halaman stabil setelah menutup detail surat
            page.wait_for_timeout(800)

            nav_success = False
            try:
                if isinstance(next_btn, str) and next_btn == "JS_CLICK":
                    raise Exception("JS_CLICK sentinel — skip ke JS")
                if self._safe_click(page, next_btn, timeout=5000):
                    nav_success = True
                else:
                    raise Exception("Safe click gagal")
            except Exception as e_click:
                if "JS_CLICK sentinel" not in str(e_click):
                    self.log(f"Klik biasa gagal ({str(e_click)[:80]}), mencoba klik paksa...", "debug")
                try:
                    if not (isinstance(next_btn, str) and next_btn == "JS_CLICK"):
                        try:
                            disabled_attr = next_btn.get_attribute("disabled")
                            aria_disabled = next_btn.get_attribute("aria-disabled")
                            class_attr = (next_btn.get_attribute("class") or "").lower()
                            class_tokens = class_attr.split()
                            if disabled_attr is not None or aria_disabled in ("true", "1") or "disabled" in class_tokens:
                                self.log("Tombol berikutnya tampak dalam keadaan disabled — tidak akan mencoba klik paksa. Menganggap akhir pagination.", "debug")
                                break
                        except Exception:
                            pass

                        if self._safe_click(page, next_btn, timeout=3000):
                            nav_success = True
                            self.log("Navigasi ke halaman berikutnya berhasil (force/alternate click).", "debug")
                except Exception:
                    pass

                if not nav_success:
                    clicked = self._click_next_page_js(page)
                    if clicked:
                        nav_success = True
                        self.log("Navigasi ke halaman berikutnya berhasil (JavaScript).", "debug")
                    else:
                        self.log(f"Tidak dapat berpindah ke halaman {page_number} — semua cara telah dicoba.", "warning")
                        break

            if not nav_success:
                break

            page.wait_for_timeout(2500)  # Tunggu halaman baru memuat
            # Pastikan list surat baru sudah muncul
            try:
                page.wait_for_selector(list_selector, timeout=8000)
            except Exception:
                self.log(f"Daftar surat tidak muncul setelah berpindah ke halaman {page_number}.", "warning")
                break

            new_snapshot = self._snapshot_page_state(page, list_selector)
            if prev_snapshot is not None and new_snapshot == prev_snapshot:
                self.log("Halaman tidak berubah setelah klik Next — menghentikan paginasi.", "warning")
                break

    def process_single_item(self, page, item_locator, menu_name, search_query=None):
        # Ambil teks baris sebelum diklik
        row_text = ""
        row_perihal = ""
        try:
            row_text = item_locator.inner_text()
            self.log(f"Membaca informasi surat dari daftar...", "debug")
            lines = [line.strip() for line in row_text.split("\n") if line.strip()]
            if len(lines) >= 2:
                # baris kedua biasanya perihal/subjek
                row_perihal = lines[1]
        except Exception as e:
            self.log(f"Tidak dapat membaca info baris surat: {str(e)}", "debug")

        # Klik item untuk membuka detail
        if not self._safe_click(page, item_locator, timeout=30000):
            raise RuntimeError("Gagal mengklik item surat. Ada overlay atau elemen lain yang menutupi klik.")
        
        # Tunggu hingga panel detail benar-benar terbuka
        # Deteksi elemen khas di dalam panel detail: tombol Cetak, Tutup, atau konten surat
        detail_opened = False
        for _det_sel in [
            "button:has-text('Cetak')",
            "button:has-text('Print')",
            "button:has-text('Tutup')",
            "button:has-text('Close')",
            "div[role='dialog']",
            "iframe",
            ".info-row",
        ]:
            try:
                page.wait_for_selector(_det_sel, timeout=5000)
                detail_opened = True
                break
            except Exception:
                pass
        
        if not detail_opened:
            # Fallback: tunggu sedikit lebih lama
            self.log("Menunggu halaman detail surat terbuka...", "debug")
            page.wait_for_timeout(4000)
        
        # Ekstrak Nomor Surat dan Perihal
        no_surat = "Tanpa_Nomor"
        perihal = "Tanpa_Perihal"
        
        try:
            text_content = ""
            iframe_loc = page.frame_locator("iframe")
            
            # Layer 1: Coba ambil dari iframe menggunakan CSS selectors (paling presisi)
            if page.locator("iframe").count() > 0:
                self.log("Membaca informasi surat dari dokumen...", "debug")
                try:
                    # Mencari elemen nomor surat
                    no_loc = iframe_loc.locator(".info-row:has-text('Nomor') .info-content")
                    if no_loc.count() > 0:
                        val = no_loc.first.inner_text().strip()
                        if val:
                            no_surat = self.sanitize_filename(val)
                            
                    # Mencari elemen perihal
                    pe_loc = iframe_loc.locator(".info-row:has-text('Perihal') .info-content")
                    if pe_loc.count() > 0:
                        val = pe_loc.first.inner_text().strip()
                        if val:
                            perihal = self.sanitize_filename(val)
                except Exception as ex_sel:
                    self.log(f"Gagal membaca info surat dari dokumen: {str(ex_sel)}", "debug")
                
                # Layer 2: Jika belum lengkap, coba regex pada text content iframe
                if no_surat == "Tanpa_Nomor" or perihal == "Tanpa_Perihal":
                    try:
                        iframe_text = iframe_loc.locator("body").inner_text()
                        text_content = iframe_text  # untuk logging debug nanti
                        
                        if no_surat == "Tanpa_Nomor":
                            no_match = re.search(r'(?:Nomor|No(?:\.|\s)?Surat|No\.?\s*:\s*|No\s*:\s*)([A-Za-z0-9\-/\\]+)', iframe_text, re.IGNORECASE)
                            if no_match:
                                no_surat = self.sanitize_filename(no_match.group(1))
                            else:
                                no_match = re.search(r'([A-Za-z0-9]{3,}[-\\/][A-Za-z0-9]+)', iframe_text)
                                if no_match:
                                    no_surat = self.sanitize_filename(no_match.group(1))
                                else:
                                    no_match = re.search(r'([A-Z0-9]{6,})', iframe_text)
                                    if no_match:
                                        no_surat = self.sanitize_filename(no_match.group(1))
                        
                        if perihal == "Tanpa_Perihal":
                            perihal_match = re.search(r'(?:Perihal|Hal)\s*:\s*([^\n]+)', iframe_text, re.IGNORECASE)
                            if perihal_match:
                                perihal = self.sanitize_filename(perihal_match.group(1))
                    except Exception as ex_reg:
                        self.log(f"Metode pembacaan alternatif gagal: {str(ex_reg)}", "debug")
            
            # Layer 3: Coba ambil dari main page text (jika iframe tidak ada atau gagal)
            if no_surat == "Tanpa_Nomor" or perihal == "Tanpa_Perihal":
                text_content = page.evaluate("() => document.body.innerText")
                
                if no_surat == "Tanpa_Nomor":
                    no_match = re.search(r'(?:Nomor|No\.?\s*Surat)\s*:\s*([^\n]+)', text_content, re.IGNORECASE)
                    if no_match:
                        no_surat = self.sanitize_filename(no_match.group(1))
                        
                if perihal == "Tanpa_Perihal":
                    perihal_match = re.search(r'(?:Perihal|Hal)\s*:\s*([^\n]+)', text_content, re.IGNORECASE)
                    if perihal_match:
                        perihal = self.sanitize_filename(perihal_match.group(1))

            # Tulis info debug jika ekstraksi gagal/sebagian
            if no_surat == "Tanpa_Nomor" or perihal == "Tanpa_Perihal":
                debug_file = Path(__file__).parent / "debug_page_text.txt"
                self.log(f"Informasi surat tidak lengkap, menyimpan catatan diagnostik.", "warning")
                try:
                    with open(debug_file, "w", encoding="utf-8") as df:
                        df.write(f"=== ROW TEXT ===\n{row_text}\n\n")
                        df.write(f"=== INNER TEXT ===\n{text_content}\n\n")
                        df.write(f"=== HTML CONTENT ===\n{page.content()}\n")
                except Exception as debug_err:
                    self.log(f"Gagal menyimpan catatan diagnostik: {str(debug_err)}", "debug")
        except Exception as e:
            self.log(f"Gagal membaca informasi surat: {str(e)}", "debug")
            
        # Layer 4: Fallback ke row_perihal jika perihal masih kosong
        if perihal == "Tanpa_Perihal" and row_perihal:
            perihal = self.sanitize_filename(row_perihal)
            self.log(f"Perihal surat diambil dari daftar: {perihal}", "debug")
            
        # Layer 5: Fallback ke search_query jika no_surat masih kosong
        if no_surat == "Tanpa_Nomor" and search_query:
            no_surat = self.sanitize_filename(search_query)
            self.log(f"Nomor surat diambil dari kata kunci pencarian: {no_surat}", "debug")
            
        # Verifikasi jika search_query disediakan
        if search_query:
            sanitized_query = self.sanitize_filename(search_query).replace("_", "").replace("-", "").lower()
            sanitized_extracted = self.sanitize_filename(no_surat).replace("_", "").replace("-", "").lower()
            
            # Cek kecocokan nomor surat secara parsial atau penuh
            if sanitized_query not in sanitized_extracted and sanitized_extracted not in sanitized_query:
                if no_surat != "Tanpa_Nomor":
                    self.log(f"Verifikasi Gagal: Nomor surat '{no_surat}' tidak cocok dengan pencarian '{search_query}'. Menutup...", "warning")
                    # Tutup detail surat untuk kembali ke daftar
                    try:
                        page.get_by_role("button", name="Close").click(timeout=2000)
                    except Exception:
                        try:
                            page.get_by_role("button", name="Tutup").click(timeout=2000)
                        except Exception:
                            pass
                    page.wait_for_timeout(1000)
                    return False
            
        # Bentuk nama folder
        folder_name = f"{no_surat} - {perihal}"
        if len(folder_name) > 150:
            folder_name = folder_name[:150]
        folder_name = self.sanitize_filename(folder_name)
        
        target_path = self.download_dir / menu_name / folder_name
        
        # Cek apakah surat ini sudah diunduh sebelumnya
        pdf_filename = f"Surat_{self.sanitize_filename(no_surat)}.pdf"
        pdf_path = target_path / pdf_filename
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            self.log(f"Surat '{no_surat}' sudah pernah diunduh ({pdf_filename} ada). Melewati...", "success")
            # Tutup detail surat untuk kembali ke daftar
            try:
                page.get_by_role("button", name="Close").click(timeout=2000)
            except Exception:
                try:
                    page.get_by_role("button", name="Tutup").click(timeout=2000)
                except Exception:
                    pass
            page.wait_for_timeout(1000)
            return True
            
        target_path.mkdir(parents=True, exist_ok=True)
        self.log(f"Membuat folder: '{menu_name}/{folder_name}'")
        
        # 1. Cetak Surat ke PDF
        pdf_saved = False
        try:
            # Coba berbagai varian nama tombol Cetak
            cetak_btn = None
            for _cetak_name in ["Cetak", "Print", "Cetak Surat", "Download PDF"]:
                _loc = page.get_by_role("button", name=_cetak_name)
                if _loc.count() > 0:
                    cetak_btn = _loc
                    break
            # Fallback: cari tombol dengan icon print
            if cetak_btn is None:
                _loc = page.locator("button:has(.lucide-printer), button:has(svg[class*='printer'])")
                if _loc.count() > 0:
                    cetak_btn = _loc.first
            
            if cetak_btn is not None:
                try:
                    # Tunggu tombol Cetak siap klik
                    cetak_btn.first.wait_for(state="visible", timeout=5000)
                    popup_page = None
                    try:
                        with page.expect_popup(timeout=10000) as popup_info:
                            if not self._safe_click(page, cetak_btn.first, timeout=10000):
                                raise RuntimeError("Gagal mengklik tombol Cetak surat.")
                        popup_page = popup_info.value
                    except Exception as print_popup_err:
                        self.log(f"Popup cetak tidak muncul: {str(print_popup_err)}", "debug")
                        page.wait_for_timeout(3000)
                        html_path = target_path / f"Surat_{no_surat}.html"
                        try:
                            with open(html_path, "w", encoding="utf-8") as f:
                                f.write(page.content())
                            self.log(f"HTML Surat berhasil disimpan sebagai fallback: Surat_{no_surat}.html", "success")
                            pdf_saved = True
                        except Exception as html_err:
                            self.log(f"Gagal menyimpan fallback HTML cetak: {str(html_err)}", "warning")

                    if popup_page:
                        self.log("Menunggu render dokumen cetak selesai...", "info")
                        try:
                            popup_page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            popup_page.wait_for_load_state("load")
                        popup_page.wait_for_timeout(4000)
                        pdf_path = target_path / f"Surat_{no_surat}.pdf"
                        try:
                            # page.pdf() hanya bekerja di Chromium Headless
                            popup_page.pdf(path=str(pdf_path))
                            self.log(f"PDF Surat berhasil disimpan: Surat_{no_surat}.pdf", "success")
                            pdf_saved = True
                        except Exception as pdf_err:
                            self.log(f"Mode cetak PDF tidak tersedia, menyimpan salinan HTML sebagai gantinya.", "debug")
                            html_path = target_path / f"Surat_{no_surat}.html"
                            content = popup_page.content()
                            with open(html_path, "w", encoding="utf-8") as f:
                                f.write(content)
                            self.log(f"HTML Surat berhasil disimpan: Surat_{no_surat}.html")
                            pdf_saved = True
                        popup_page.close()
                    else:
                        # Jika popup tidak muncul, cek apakah dokumen cetak ditampilkan di iframe tersembunyi
                        try:
                            iframe = page.locator("iframe").first
                            if iframe.count() > 0:
                                html_path = target_path / f"Surat_{no_surat}.html"
                                try:
                                    handle = iframe.element_handle()
                                    if handle:
                                        srcdoc = page.evaluate("el => el.srcdoc || el.contentDocument.documentElement.outerHTML", handle)
                                        if srcdoc:
                                            with open(html_path, "w", encoding="utf-8") as f:
                                                f.write(srcdoc)
                                            self.log(f"HTML Surat berhasil disimpan dari iframe fallback: Surat_{no_surat}.html", "success")
                                            pdf_saved = True
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception as e_click:
                    self.log(f"Tidak dapat mencetak surat ke PDF: {str(e_click)}", "debug")
            else:
                self.log("Tombol cetak tidak tersedia untuk surat ini.", "debug")
        except Exception as e:
            self.log(f"Gagal memproses pencetakan dokumen: {str(e)}", "warning")
            
        # 2. Download Lampiran jika ada
        try:
            # Coba berbagai varian nama tombol Lampiran
            lampiran_btn = None
            for _lamp_name in ["Lampiran & Referensi", "Lampiran", "Referensi", "Attachment", "Attachments"]:
                _loc = page.get_by_role("button", name=_lamp_name)
                if _loc.count() > 0:
                    lampiran_btn = _loc
                    break
            if lampiran_btn is None:
                _loc = page.locator("button:has-text('Lampiran')")
                if _loc.count() > 0:
                    lampiran_btn = _loc.first

            if lampiran_btn is not None:
              try:
                # Klik tombol lampiran
                if not self._safe_click(page, lampiran_btn.first, timeout=6000):
                    raise RuntimeError("Gagal mengklik tombol Lampiran.")
                page.wait_for_timeout(2000)
                
                # Ambil semua link lampiran dari dialog modal yang terbuka
                attachment_elements = page.locator('div[role="dialog"] a[href]')
                count = attachment_elements.count()
                
                attachment_links = []
                for i in range(count):
                    el = attachment_elements.nth(i)
                    href = el.get_attribute("href")
                    text = el.inner_text().strip() or el.get_attribute("title") or ""
                    
                    if href and href.startswith("http") and text:
                        clean_name = self.sanitize_filename(text)
                        # Pastikan tidak ada duplikasi URL
                        if not any(item['href'] == href for item in attachment_links):
                            attachment_links.append({
                                'href': href,
                                'name': clean_name
                            })
                            
                if attachment_links:
                    self.log(f"Menemukan {len(attachment_links)} berkas lampiran. Mulai mengunduh...")
                    import urllib.request
                    
                    for idx, att in enumerate(attachment_links):
                        self.check_stop()
                        filename = att['name']
                        self.log(f"Mengunduh lampiran [{idx+1}/{len(attachment_links)}]: {filename}...")
                        
                        try:
                            # Unduh menggunakan HTTP GET langsung lewat urllib (Signed URL)
                            file_dest = target_path / filename
                            urllib.request.urlretrieve(att['href'], str(file_dest))
                            self.log(f"Unduh sukses: {filename}", "success")
                        except Exception as dl_err:
                            self.log(f"Unduhan langsung gagal, mencoba cara lain...", "warning")
                            # Fallback: Klik lewat browser
                            try:
                                # Cari locator link berdasarkan href
                                target_link = page.locator(f'div[role="dialog"] a[href="{att["href"]}"]').first
                                with page.expect_popup(timeout=15000) as popup_info:
                                    target_link.click()
                                popup_page = popup_info.value
                                popup_page.wait_for_load_state()
                                
                                # Coba expect download saat mengklik tombol Download di popup
                                download_btn = None
                                if popup_page.get_by_role("button", name="Download").count() > 0:
                                    download_btn = popup_page.get_by_role("button", name="Download").first
                                elif popup_page.locator("text=Download").count() > 0:
                                    download_btn = popup_page.locator("text=Download").first
                                    
                                if download_btn:
                                    with popup_page.expect_download(timeout=20000) as download_info:
                                        download_btn.click()
                                    download = download_info.value
                                    download.save_as(str(target_path / filename))
                                    self.log(f"Unduh sukses (browser): {filename}", "success")
                                else:
                                    # Jika tidak ada tombol download, simpan pdf / screenshot popup
                                    popup_page.pdf(path=str(target_path / filename))
                                    self.log(f"Unduh sukses (cetak PDF popup): {filename}", "success")
                                popup_page.close()
                            except Exception as fb_err:
                                self.log(f"Gagal mengunduh {filename} dengan semua metode: {str(fb_err)}", "error")
                else:
                    self.log("Surat ini tidak memiliki berkas lampiran.", "debug")
              except Exception as e_lampiran:
                  self.log(f"Tidak dapat membuka panel lampiran: {str(e_lampiran)}", "debug")
            else:
                self.log("Surat ini tidak memiliki lampiran.", "debug")
        except Exception as e:
            self.log(f"Tidak dapat memeriksa lampiran: {str(e)}", "debug")
            
        # 3. Tutup Detail Surat untuk kembali ke list
        try:
            self.log("Menutup detail surat...")
            tutup_clicked = False
            for _close_name in ["Close", "Tutup", "Kembali", "Back"]:
                try:
                    _btn = page.get_by_role("button", name=_close_name)
                    if _btn.count() > 0:
                        _btn.first.click(timeout=3000)
                        tutup_clicked = True
                        break
                except Exception:
                    pass
            
            if not tutup_clicked:
                # Fallback: cari tombol dengan icon X atau Escape key
                try:
                    _x_btn = page.locator("button.close, button[aria-label='Close'], button[aria-label='Tutup'], div[role='dialog'] button:last-child")
                    if _x_btn.count() > 0:
                        _x_btn.first.click(timeout=2000)
                        tutup_clicked = True
                except Exception:
                    pass
            
            if not tutup_clicked:
                # Last resort: tekan Escape
                page.keyboard.press("Escape")
                self.log("Kembali ke daftar surat.", "debug")
            
            page.wait_for_timeout(1500)
        except Exception as e_close:
            self.log(f"Tidak dapat menutup detail surat: {str(e_close)}", "debug")
            
        return True
