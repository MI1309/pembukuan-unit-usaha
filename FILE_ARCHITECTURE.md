# File-by-File Architecture Document

## Bot WhatsApp Automation PT Lentera Agrinova Organik

Dokumen ini memetakan seluruh **26 file Python** di bawah `app/` berdasarkan kode saat ini. Fungsi yang tidak memiliki anotasi tipe di source ditandai sebagai **tipe inferensi**. Daftar dependensi berfokus pada import langsung; alur runtime tambahan disebutkan pada catatan arsitektur.

## 1. Entry Point & Config

### 📄 `app/main.py`
**Peran Utama:** Merakit aplikasi FastAPI, memasang router HTTP, dan menyediakan health check ASGI.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada komposisi aplikasi: file ini menghubungkan router dan tidak menjalankan aturan bisnis transaksi.

**Dependensi (Import):** `fastapi.FastAPI`; `app.routers.report_router`; `app.routers.webhook`.

**Fungsi/Class Publik:**
- `app: FastAPI`: Instance aplikasi yang memasang router webhook dan report.
- `health_check() -> dict` (tipe hasil inferensi): Mengembalikan status aktif aplikasi pada `GET /`.

**Catatan Arsitektur:** Ini entry point ASGI. Deployment PythonAnywhere yang didokumentasikan menggunakan `pythonanywhere_wsgi.application`, bukan instance ini, sehingga route yang didefinisikan melalui FastAPI tidak otomatis tersedia di dispatcher WSGI.

### 📄 `app/config.py`
**Peran Utama:** Memuat konfigurasi environment, path credential, aturan bisnis, katalog produk, dan daftar unit usaha.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai pusat konfigurasi; dependency konfigurasi tidak disebar sebagai pembacaan environment di setiap service.

**Dependensi (Import):** Standard library `os`, `pathlib.Path`; package eksternal `dotenv.load_dotenv`.

**Fungsi/Class Publik:**
- `Config`: Kumpulan atribut konfigurasi integrasi, bisnis, payroll, produk, unit, dan rekap otomatis.
- `config: Config`: Singleton module-level yang digunakan modul aplikasi.

**Catatan Arsitektur:** `.env` dimuat dari root project. `SPREADSHEET_ID`, token Fonnte/Groq, webhook secret dan ID folder backup berasal dari environment. `RECAP_ENABLED`, `RECAP_LAZY_TRIGGER_ENABLED`, dan `RECAP_BOUNDARY_DAYS` mengontrol fitur subtotal otomatis.

### 📄 `app/models.py`
**Peran Utama:** Mendefinisikan model Pydantic untuk struktur data transaksi, respons Groq, dan payload webhook.

**Prinsip SOLID yang Dijaga:** Single Responsibility untuk kontrak data dan validasi bentuk model.

**Dependensi (Import):** `pydantic.BaseModel`; `typing.List`, `typing.Optional`.

**Fungsi/Class Publik:**
- `SalesItem`: Model transaksi dengan produk, kategori, keterangan, kuantitas, satuan, nominal penjualan, dan tanggal.
- `GroqResponse`: Model berisi daftar objek transaksi.
- `WebhookPayload`: Model payload dengan `sender`, `message`, dan `secret` opsional.

**Catatan Arsitektur:** Model tersedia sebagai kontrak terstruktur, tetapi router webhook yang berjalan saat ini mem-parsing request ke dictionary secara manual; jangan mengasumsikan semua request telah divalidasi menggunakan model ini.

### 📄 `app/auth_drive.py`
**Peran Utama:** Menjadi entry point CLI untuk menjalankan otorisasi OAuth awal Google Drive.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai wrapper command-line; implementasi OAuth diletakkan pada service.

**Dependensi (Import):** `app.services.drive_oauth.authorize_drive`.

**Fungsi/Class Publik:**
- Tidak mendefinisikan fungsi atau class publik; saat dijalankan sebagai modul/script, memanggil `authorize_drive()`.

**Catatan Arsitektur:** Jalankan secara eksplisit saat menyiapkan token Drive. Flow menggunakan browser lokal dan menulis token ke path konfigurasi; bukan bagian dari request webhook.

### 📄 `app/backup_task.py`
**Peran Utama:** Menyediakan command-line task untuk menjalankan backup harian dari scheduler eksternal.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai adapter CLI; operasi backup dan cleanup tetap menjadi tanggung jawab `backup_service`.

**Dependensi (Import):** Standard library `sys`; `app.config.config`; `app.services.backup_service.daily_backup`.

**Fungsi/Class Publik:**
- `main() -> int`: Memeriksa konfigurasi backup, menjalankan backup harian, mencetak hasil dan mengembalikan exit code sukses/gagal.

**Catatan Arsitektur:** Dijalankan misalnya melalui PythonAnywhere Scheduled Task dengan `python -m app.backup_task`. File ini tidak memasang scheduler dan tidak berjalan otomatis hanya karena web app aktif.

## 2. Core Layer

### 📄 `app/core/utils.py`
**Peran Utama:** Menyediakan helper zona waktu Jakarta, format tanggal/mata uang Indonesia, dan parsing nominal.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai kumpulan utilitas lintas domain, bukan tempat aturan bisnis atau akses database.

**Dependensi (Import):** Standard library `re`, `datetime.datetime`; package eksternal `pytz`; `app.config.config`.

**Fungsi/Class Publik:**
- `now_tz() -> datetime`: Mengembalikan waktu saat ini pada zona waktu konfigurasi.
- `format_tanggal_id(date_obj: datetime) -> str`: Memformat tanggal menggunakan nama bulan Indonesia.
- `format_rupiah(value) -> str`: Mengubah angka menjadi teks berformat Rupiah dengan pemisah ribuan titik.
- `parse_amount(value) -> int`: Mengubah angka/teks nominal, termasuk akhiran ribu/juta, menjadi integer.

**Catatan Arsitektur:** Helper ini bebas dari I/O eksternal. `parse_amount` menggunakan aturan separator lokal; untuk data format baru, perubahan perlu diuji agar tidak salah membaca koma/titik desimal.

### 📄 `app/core/whatsapp.py`
**Peran Utama:** Mengirim pesan WhatsApp melalui Fonnte dan menangani retry serta notifikasi kondisi kuota.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai adapter komunikasi keluar; service domain tidak membentuk request HTTP Fonnte secara langsung.

**Dependensi (Import):** Standard library `re`, `time`, `threading.Lock`; package eksternal `requests`; `app.config.config`; `app.core.utils.now_tz`.

**Fungsi/Class Publik:**
- `send_whatsapp(target: str, message: str) -> bool`: Mengirim pesan dengan timeout dan retry terbatas; melaporkan keberhasilan pengiriman sebagai boolean.

**Catatan Arsitektur:** Pemanggil perlu memeriksa nilai boolean untuk membedakan pesan terkirim dan gagal. Notifikasi kuota dibatasi sekali per tanggal dalam state proses lokal; log error saat ini menggunakan `print`, bukan logger terstruktur.

### 📄 `app/core/groq_ai.py`
**Peran Utama:** Mem-parsing pesan transaksi menjadi JSON terstruktur menggunakan jalur format eksplisit atau API Groq.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai adapter AI; tidak membaca atau menulis Google Sheets.

**Dependensi (Import):** Standard library `json`, `re`, `time`; package eksternal `requests`; `app.config.config`; `app.core.utils.now_tz`.

**Fungsi/Class Publik:**
- `analyze_sales(message: str) -> dict`: Mengembalikan transaksi hasil parser format pipa lokal bila cocok; selain itu memanggil Groq dan mengembalikan struktur dictionary transaksi.

**Catatan Arsitektur:** Prompt dibentuk dari katalog/unit konfigurasi, dan respons diharapkan berupa JSON. Request memiliki timeout 20 detik serta retry terbatas untuk gangguan jaringan dan status tertentu. Validasi produk, nominal, unit tujuan, dan persistensi dilakukan oleh `sales_service`, bukan modul ini.

### 📄 `app/core/retry.py`
**Peran Utama:** Menyediakan kebijakan retry terpusat untuk Google API dan helper append yang memeriksa idempotensi sebelum mengulang operasi.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai utilitas resilience lintas repository; modul tidak mengakses Google Sheets secara langsung.

**Dependensi (Import):** Standard library `logging`, `random`, `time`, `datetime`, `email.utils`, `functools`; `requests` diimpor secara lazy untuk mengenali timeout/koneksi.

**Fungsi Publik:**
- `retry_google_api(max_attempts=5, base_delay=1.0, max_delay=30.0, jitter=True)`: Decorator yang mencoba ulang status HTTP 429, 500, 502, 503, dan 504, menggunakan exponential backoff, jitter, serta header `Retry-After` bila tersedia.
- `safe_append_with_idempotency(operation, transaction_ids, find_existing, ...)`: Mengulang append setelah error transient hanya jika ID transaksi belum ditemukan; jika seluruh ID sudah tersimpan, hasil append dianggap berhasil.
- `error_code(error) -> int | None`: Mengambil kode status dari exception untuk keputusan retry dan logging.

**Catatan Arsitektur:** Retry mencatat attempt, delay, dan kode error pada level WARNING tanpa mencetak pesan exception mentah. Append tanpa Transaction ID tidak diulang karena repository tidak dapat memastikan apakah server sudah menerapkannya; timeout/koneksi hanya dipertimbangkan pada helper append yang memiliki pemeriksaan ID.

### 📄 `app/core/transaction_id.py`
**Peran Utama:** Membuat Transaction ID deterministik untuk ledger, detail harian, dan arsip.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai pembentuk identitas transaksi yang tidak bergantung pada Google Sheets atau router.

**Dependensi (Import):** Standard library `hashlib`, `re`, `datetime.date/datetime`.

**Fungsi Publik:**
- `generate_transaction_id(sender, tanggal, produk, kuantitas, nominal, keterangan, unit) -> str`: Menghasilkan `{YYYYMMDD}-{UNIT_CODE}-{HASH8}` dari field transaksi yang dinormalisasi.

**Trigger deteksi duplikat otomatis:**
- Deteksi duplikat tidak terjadi saat file dibuka atau saat webhook hanya menerima pesan. Trigger yang benar adalah saat proses bisnis sudah menyelesaikan normalisasi dan akan menulis ke Google Sheets.
- Sistem membuat `transaction_id` dari sender, tanggal, produk, kuantitas, nominal, dan keterangan; lalu `sheet_repo.find_transaction_ids()` mengecek kolom `Transaction ID` di ledger/detail sebelum `append_rows_atomically()`.
- Jika ID sudah ada, transaksi diangap duplikat dan dilewati dengan status `DUPLICATE_SKIPPED` tanpa menambah baris baru. Ini mencegah double entry walaupun pesan/permintaan datang ulang.

**Catatan Arsitektur:** Kode unit yang dikenal mencakup PRT, TAF, TAE, IKN, dan PPK; unit yang tidak dikenali menghasilkan `UNK`. `sales_service` mengekspos wrapper dengan nama fungsi sama.

## 3. Repository Layer

### 📄 `app/repositories/sheet_repo.py`
**Peran Utama:** Mengenkapsulasi koneksi dan operasi Google Sheets, cache worksheet/record, batch write, serta pengarsipan data.

**Prinsip SOLID yang Dijaga:** Repository Pattern memisahkan akses gspread dari aturan bisnis service; tanggung jawabnya tetap berpusat pada persistence spreadsheet.

**Dependensi (Import):** Standard library `hashlib`, `json`, `logging`, `re`, `datetime`, `threading.RLock`, `time.monotonic`; package eksternal `gspread`, `oauth2client.service_account.ServiceAccountCredentials`, `gspread.utils.numericise`; `app.config.config`; `app.core.retry`; `app.core.transaction_id`; `app.core.utils.parse_amount`. OAuth Drive dan pencatatan failure arsip diambil secara lazy dari service terkait.

**Fungsi/Class Publik:**
- `get_client()`: Membuat/mengembalikan gspread client yang diotorisasi menggunakan Service Account.
- `get_spreadsheet()`: Membuka/mengembalikan spreadsheet berdasarkan ID konfigurasi.
- `get_sheet(name: str, create=True)`: Mengambil worksheet dari cache atau spreadsheet; dapat membuat sheet jika diizinkan.
- `invalidate_cache(sheet_name: str) -> None`: Menghapus cache record untuk worksheet setelah perubahan.
- `append_row(sheet_name: str, row: list)`: Menambahkan satu baris dan menginvalidasi cache.
- `append_rows_atomically(rows_by_sheet: list[tuple[str, list]]) -> None`: Mengirim beberapa append lintas worksheet dalam satu batch update Google Sheets.
- `archive_daily_detail(archive_month: str, rows: list[list], new_period_label: str) -> int`: Menyimpan detail harian ke sheet arsip dan membersihkan baris periode lama dari sumber.
- `update_cell(sheet_name: str, row: int, column: int, value)`: Memperbarui sel dengan koordinat berbasis satu.
- `delete_row(sheet_name: str, row: int)`: Menghapus baris worksheet jika tersedia.
- `get_drive_access_token() -> str`: Meminta access token OAuth Drive melalui `drive_oauth`.
- `get_sales_data()`: Membaca records dari worksheet legacy `Rincian Penjualan`.
- `get_all_records_many(sheet_names: list[str]) -> dict[str, list[dict]]`: Membaca dan mengubah values menjadi records untuk satu/beberapa sheet.
- `get_all_records(sheet_name: str) -> list`: Convenience wrapper untuk membaca records satu sheet.
- `find_transaction_ids(sheet_names: list[str], tx_ids: list[str]) -> dict[str, list[str]]`: Mencari ID hanya pada kolom `Transaction ID` dan mengembalikan worksheet yang memuat tiap ID.
- `find_subtotal_rows(sheet_name: str, label_prefix: str) -> list[int]`: Mencari index baris subtotal berdasarkan prefix label.
- `insert_rows_after(sheet_name: str, after_row: int, rows: list[list]) -> None`: Menambah baris setelah index tertentu.
- `rewrite_period_block(sheet_name: str, start_row: int, end_row: int, new_rows: list[list]) -> None`: Menulis ulang blok baris secara atomik.
- `delete_rows_bottom_up(sheet_name: str, row_indices: list[int]) -> int`: Menghapus beberapa baris sekaligus dari bawah ke atas.
- `archive_unit_transactions(rows_by_unit: dict, archived_at: str) -> int`: Menyalin baris, memverifikasi set ID dan menulis manifest `_Meta` sebelum menghapus sumber; mismatch menghasilkan `ArchiveVerificationError` tanpa delete.
- `verify_archive_manifest(manifest_key: str) -> dict`: Membandingkan count dan hash ID arsip dengan manifest.
- `rollback_archive(manifest_key: str, apply: bool = True) -> dict`: Menghitung atau memulihkan baris ledger yang hilang dari arsip yang lolos verifikasi.
- `ArchiveVerificationError`: Error arsip dengan atribut `missing_ids` dan `extra_ids`.

**Catatan Arsitektur:** Credential Sheets berasal dari Service Account `credentials.json`; akses Drive backup memakai OAuth terpisah. Client, worksheet cache, lock, dan record cache bersifat global dalam satu proses; cache record memiliki TTL sekitar tiga detik. Append dengan ID memeriksa keberadaan ID sebelum retry; delete memverifikasi ulang baris. Copy arsip dan delete bukan satu transaksi API, tetapi manifest ditulis setelah verifikasi dan sebelum delete agar partial-delete dapat dipulihkan.

## 4. Service Layer

### 📄 `app/services/sales_service.py`
**Peran Utama:** Menjalankan use-case transaksi: normalisasi/validasi penjualan dan pembelian, pemilihan unit, penyimpanan, arsip, laporan pesan, dan bantuan.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada domain transaksi, dengan parsing eksternal didelegasikan ke adapter Groq dan persistence ke repository.

**Dependensi (Import):** Standard library `calendar`, `re`, `datetime.date/datetime/timedelta`, `threading.RLock`; `app.config.config`; `app.core.groq_ai.analyze_sales`; helper format/tanggal/nominal; `app.repositories.sheet_repo`; `app.core.whatsapp.send_whatsapp`.

**Fungsi/Class Publik:**
- `get_unit_sheet(produk_nama: str) -> str`: Memetakan nama produk penjualan ke worksheet unit.
- `generate_transaction_id(sender, tanggal, produk, kuantitas, nominal, keterangan, unit) -> str`: Wrapper generator ID dari `app.core.transaction_id`.
- `process_sales(message: str, confirmed: bool = False, sender: str = "") -> tuple[bool, str, dict | None]`: Mem-parsing dan memvalidasi transaksi, menunggu konfirmasi jika nominal besar, memeriksa ID duplikat, atau menyimpan baris yang valid. `sender` opsional untuk kompatibilitas pemanggil lama.
- `process_rekap(message: str) -> str | None`: Membentuk rekap transaksi untuk periode yang diminta melalui pesan.
- `save_daily_recap(target: date | None = None) -> None`: Mengganti rekap harian pada worksheet recap.
- `save_monthly_recap(target: date | None = None) -> None`: Mengganti rekap bulanan pada worksheet recap.
- `send_daily_recap() -> str`: Mengirim rekap harian ke nomor notifikasi dan menyimpannya.
- `send_previous_month_recap() -> str`: Mengirim serta menyimpan rekap bulan sebelumnya.
- `_lazy_trigger_recap(now: datetime, trigger_source: str) -> None`: Memicu perhitungan subtotal/total secara lazy jika melewati batas periode.
- `handle_help_command(message: str) -> str | None`: Menghasilkan panduan bot untuk perintah bantuan yang dikenali.
- `help_copy_messages() -> list[str]`: Mengembalikan contoh pesan transaksi siap salin.

**Catatan Arsitektur:** Service ini melakukan orkestrasi parsing, perhitungan, pengarsipan, dan persistence. ID yang sudah ada menghasilkan respons duplicate dan log `DUPLICATE_SKIPPED`; Transaction ID berbasis konten juga menangani redelivery tanpa event ID. Pemetaan penjualan otomatis hanya mencakup kategori yang ditentukan di kode; pembelian memerlukan unit yang disebut eksplisit. Lock penyimpanan hanya melindungi proses lokal.

### 📄 `app/services/karyawan_service.py`
**Peran Utama:** Mengelola profil karyawan, pencocokan nomor dan role, perintah karyawan/admin, presensi, serta statistik kehadiran.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada domain karyawan dan presensi; perhitungan serta penyimpanan payroll utama berada di `payroll_service`.

**Dependensi (Import):** Standard library `calendar`, `re`, `datetime.date/datetime`, `threading.RLock`; `app.config.config`; helper Rupiah/waktu/nominal; `app.repositories.sheet_repo`. Memuat `payroll_service` secara lazy untuk beberapa perintah gaji.

**Fungsi/Class Publik:**
- `normalize_phone(phone: str) -> str`: Menghapus karakter non-digit dan mengubah awalan lokal `0` ke kode negara `62`.
- `normalize_nik(nik) -> str`: Menormalkan NIK numerik dengan padding tiga digit.
- `is_admin(sender: str) -> bool`: Membandingkan nomor dengan konfigurasi admin atau fallback allowlist.
- `get_karyawan(nik: str) -> dict | None`: Mencari profil menurut NIK.
- `get_karyawan_by_phone(phone: str, active_only: bool = False) -> dict | None`: Mencari profil berdasarkan nomor yang dinormalisasi.
- `parse_form_karyawan(raw: str) -> dict`: Mengubah formulir teks berlabel menjadi dictionary nilai karyawan.
- `add_karyawan(raw: str) -> str`: Memvalidasi dan menambahkan data karyawan ke worksheet.
- `update_karyawan(nik: str, field: str, value: str) -> str`: Mengubah field profil yang diizinkan.
- `set_status_karyawan(nik: str, active: bool) -> str`: Mengubah status aktif/nonaktif.
- `delete_karyawan(nik: str) -> str`: Menghapus profil; riwayat presensi tidak ikut dihapus.
- `record_attendance(employee: dict, action: str, reason: str = "", ymd: str | None = None, by_admin: bool = False) -> str`: Mencatat atau mengoreksi presensi.
- `attendance_stats(employee: dict, period: str | None = None, records: list[dict] | None = None) -> dict`: Menghitung statistik presensi periode, termasuk alpha otomatis jika diaktifkan.
- `is_help_command(message: str, admin: bool = False) -> bool`: Mengidentifikasi perintah bantuan karyawan.
- `help_copy_messages(admin: bool = False) -> list[str]`: Mengembalikan contoh perintah karyawan/admin.
- `handle_karyawan(message: str, sender: str) -> str | None`: Merutekan perintah karyawan dan admin dalam domain ini.

**Catatan Arsitektur:** Banyak fungsi helper internal berawalan `_` membaca worksheet langsung melalui repository. `is_admin()` memakai `ADMIN_SENDERS` atau fallback `ALLOWED_SENDERS`; bila konfigurasi keduanya kosong, kode mengizinkan semua sender. Lock penulisan bersifat lokal proses.

### 📄 `app/services/payroll_service.py`
**Peran Utama:** Menghitung payroll, mencatat bonus/potongan, menyimpan slip gaji, dan menangani perintah payroll.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada kalkulasi dan use-case penggajian, dengan data karyawan/presensi disediakan oleh `karyawan_service` dan repository.

**Dependensi (Import):** Standard library `re`, `datetime.datetime`; `app.config.config`; helper format/waktu/nominal; `app.repositories.sheet_repo`; `app.services.karyawan_service`.

**Fungsi/Class Publik:**
- `calculate_payroll(employee: dict, period: str | None = None, attendance_records: list[dict] | None = None, adjustment_records: list[dict] | None = None) -> dict`: Menghitung komponen dan hasil gaji bersih.
- `payroll_slip(employee: dict, period: str | None = None, save: bool = True) -> str`: Membuat teks slip dan secara default menyimpan/memperbarui baris slip.
- `create_adjustment(nik: str, adjustment_type: str, amount, description: str = "", recorded_by: str = "") -> str`: Mencatat bonus atau potongan untuk periode berjalan.
- `process_monthly_payroll(period: str | None = None, save: bool = False) -> str`: Menghitung rekap semua karyawan aktif dan opsional menyimpan seluruh slip.
- `handle_payroll_command(message: str, sender: str) -> str | None`: Memproses perintah rekap/proses gaji, slip, bonus, dan potongan.

**Catatan Arsitektur:** Service memanggil helper privat `karyawan_service` untuk statistik, resolusi target, dan label periode. Aturan nominal seperti tunjangan, potongan telat, hari kerja, dan nilai lembur berasal dari konfigurasi; perubahan aturan harus ditinjau sebagai perubahan kebijakan bisnis.

### 📄 `app/services/report_service.py`
**Peran Utama:** Mengagregasi transaksi unit usaha untuk rentang tanggal dan mengganti hasil periode pada worksheet rekap.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada reporting tersimpan; parsing perintah rekap WhatsApp tetap berada di `sales_service`.

**Dependensi (Import):** Standard library `calendar`, `re`, `collections.defaultdict`, `datetime.date/datetime`, `threading.RLock`; `app.config.config`; helper Rupiah/nominal; `app.repositories.sheet_repo`.

**Fungsi/Class Publik:**
- `generate_laporan_periode(unit_usa: str, start_date: str, end_date: str) -> dict`: Menghitung pemasukan, pengeluaran, dan laba/rugi untuk satu unit dan rentang tanggal.
- `simpan_laporan_ke_sheet(periode_name: str, start_date: str, end_date: str) -> dict`: Menghitung semua unit, mengganti baris periode yang sama, lalu menyimpan hasil rekap.
- `jalankan_rekap_mingguan(target_month: str) -> str`: Menyimpan laporan dalam empat rentang tanggal tetap untuk bulan yang diminta.
- `jalankan_rekap_bulanan(target_month: str) -> str`: Menyimpan satu rekap untuk seluruh rentang bulan.

**Catatan Arsitektur:** Service menghapus baris label periode lama sebelum append hasil baru, sehingga eksekusi ulang mengganti periode tersebut. Minggu laporan adalah blok 1-7, 8-14, 15-21, 22-akhir bulan, bukan minggu kalender. Worksheet tujuan harus sudah tersedia.

### 📄 `app/services/periodic_total_service.py`
**Peran Utama:** Mengelola logika periode (mingguan/bulanan), perhitungan subtotal/total, pengurutan blok transaksi, dan penulisan baris total ke ledger.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada agregasi periodik; memisahkan logika rekap otomatis dari pencatatan transaksi harian.

**Dependensi (Import):** Standard library `calendar`, `logging`, `dataclasses`, `datetime`, `threading.RLock`; `app.config.config`; `app.core.utils`; `app.repositories.sheet_repo`.

**Fungsi/Class Publik:**
- `Period`: Dataclass untuk label dan rentang tanggal periode.
- `PeriodTotal`: Dataclass untuk hasil agregat periode.
- `get_period_boundaries(year, month)`: Menghasilkan 4 minggu + 1 bulan sebagai objek Period.
- `compute_period_total(sheet_name, period)`: Menghitung Pemasukan, Pengeluaran, dan Saldo Akhir untuk satu periode.
- `ensure_sorted(sheet_name, period)`: Memastikan transaksi dalam blok periode terurut berdasarkan Tanggal dan Transaction ID.
- `write_period_total(sheet_name, period)`: Menulis baris SUBTOTAL MINGGU ke ledger.
- `write_monthly_total(sheet_name, year, month)`: Menulis baris TOTAL BULAN ke ledger.
- `process_all_units_period(year, month, period_kind, week_index)`: Menjalankan penulisan total untuk semua unit usaha.

**Catatan Arsitektur:** Baris subtotal ditandai dengan label khusus di kolom Keterangan. Perhitungan saldo akhir mengambil saldo baris transaksi terakhir dalam periode atau saldo terakhir sebelum periode dimulai jika kosong.

### 📄 `app/services/rekap_service.py`
**Peran Utama:** Menyediakan formatter rekap penjualan sederhana.

**Prinsip SOLID yang Dijaga:** Single Responsibility sebagai formatter agregat ringan.

**Dependensi (Import):** `app.core.utils.format_rupiah`; import lokal tertunda atas helper privat `app.services.sales_service._sales_rows`.

**Fungsi/Class Publik:**
- `build_rekap_bulanan() -> str`: Menghitung jumlah nominal dari baris penjualan yang dibaca service sales dan mengembalikan teks ringkas atau pesan bahwa data belum tersedia.

**Catatan Arsitektur:** Meski bernama bulanan, implementasi saat ini menjumlahkan hasil `_sales_rows()` tanpa filter periode dan tidak menyimpan rekap. Ketergantungan pada fungsi privat service lain menunjukkan batas modul belum sepenuhnya terpisah; cek penggunaan sebelum mengembangkan file ini.

### 📄 `app/services/setup_service.py`
**Peran Utama:** Menyiapkan dan memformat worksheet ledger unit, employee/payroll, serta worksheet rekap.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada setup struktur operasional spreadsheet; schema referensi utama didefinisikan oleh `setup_reference_structure`.

**Dependensi (Import):** Standard library `re`; `app.repositories.sheet_repo`; `app.config.config`; `app.setup_reference_structure.setup_reference_structure`.

**Fungsi/Class Publik:**
- `setup_sheet_unit(sheet_name)` (return type tidak dianotasi): Membuat/memperbarui header dan format worksheet ledger unit.
- `init_all_sheets()` (hasil inferensi `bool`): Menerapkan struktur referensi, menyiapkan unit, metadata, serta formatting sheet karyawan dan rekap; mengembalikan status berhasil/gagal.

**Catatan Arsitektur:** Setup melakukan operasi tulis dan formatting Google Sheets. Pemanggil harus membatasi aksesnya; route `GET /setup` saat ini memicu perubahan struktur.

### 📄 `app/services/backup_service.py`
**Peran Utama:** Membuat salinan spreadsheet ke Drive, menerapkan retensi, membersihkan log/konfirmasi lama, dan mencatat status backup.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada lifecycle backup; pengelolaan token OAuth didelegasikan ke `drive_oauth` melalui repository.

**Dependensi (Import):** Standard library `datetime`, `threading.RLock`; package eksternal `requests`, `pytz`; `app.config.config`; `app.core.whatsapp.send_whatsapp`; `app.core.utils.now_tz`; `app.repositories.sheet_repo`; `app.services.webhook_state.log_event`.

**Fungsi/Class Publik:**
- `create_backup_copy(folder_id: str | None = None) -> dict`: Membuat satu salinan spreadsheet ke folder Drive dan mengembalikan metadata nama/ID.
- `manual_backup() -> dict`: Menjalankan satu kali backup manual tanpa retensi dan cleanup.
- `daily_backup() -> dict`: Menjalankan backup harian, menghapus salinan yang melewati retensi, memangkas log, membersihkan konfirmasi kedaluwarsa, dan mencatat hasil.
- `prune_logs(days: int = 90) -> int`: Menghapus baris `_Logs` yang lebih tua dari batas hari.
- `cleanup_pending_confirmations() -> int`: Mengosongkan data pada baris konfirmasi yang kedaluwarsa atau invalid.

**Catatan Arsitektur:** Integrasi Drive menggunakan access token OAuth akun pengguna, bukan Service Account Sheets. Operasi harian terdiri dari beberapa efek eksternal; kegagalan parsial tidak otomatis membatalkan backup atau cleanup yang sudah terjadi.

### 📄 `app/services/drive_oauth.py`
**Peran Utama:** Mengelola token OAuth pengguna untuk Google Drive, termasuk otorisasi awal dan refresh token.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada autentikasi Drive; operasi salin file ada di `backup_service`.

**Dependensi (Import):** Standard library `pathlib.Path`; package eksternal `google.auth.transport.requests.Request`, `google.oauth2.credentials.Credentials`, `google_auth_oauthlib.flow.InstalledAppFlow`; `app.config.config`.

**Fungsi/Class Publik:**
- `get_drive_credentials() -> Credentials`: Memuat token pengguna, mengembalikannya bila valid, atau merefresh lalu menyimpan ulang token.
- `authorize_drive() -> None`: Menjalankan flow OAuth Desktop lokal dan menyimpan token otorisasi.

**Catatan Arsitektur:** File token berbeda dari `credentials.json` service account yang digunakan Sheets. Token ditulis dengan permission `0600`; lindungi file dan refresh token karena dapat memberikan akses Drive.

### 📄 `app/services/webhook_state.py`
**Peran Utama:** Menyediakan state pemrosesan webhook: deduplikasi event, rate limit, konfirmasi tertunda, dan logging.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada state operasional webhook, terpisah dari routing dan aturan transaksi.

**Dependensi (Import):** Standard library `hashlib`, `re`, `datetime.datetime/timedelta`, `threading.RLock`; package eksternal `pytz`; `app.config.config`; `app.core.utils.now_tz`; `app.repositories.sheet_repo`.

**Fungsi/Class Publik:**
- `is_duplicate_event(event_id: str | None) -> bool`: Memeriksa fingerprint event dalam TTL proses.
- `is_rate_limited(sender: str) -> bool`: Membatasi jumlah event per pengirim dalam bucket menit.
- `save_pending_confirmation(sender: str, message: str) -> None`: Menyimpan pesan yang menunggu konfirmasi ke `_PendingConfirm` beserta waktu expiry.
- `get_pending_confirmation(sender: str) -> str | None`: Mencari konfirmasi yang masih berlaku dan membersihkan entri kedaluwarsa saat dibaca.
- `clear_pending_confirmation(sender: str) -> None`: Mengosongkan data konfirmasi pengirim.
- `log_event(sender: str, message: str, status: str, invoice: str = "", error: str = "") -> None`: Memastikan header `_Logs` dan menambahkan catatan event.

**Catatan Arsitektur:** Deduplikasi dan rate-limit disimpan di dictionary/lock proses lokal, sehingga hilang saat restart dan tidak dibagi antar-worker. Pending confirmation serta log disimpan di Sheets. Log menyimpan pesan mentah sampai 500 karakter; pertimbangkan kontrol akses dan minimisasi data.

## 5. Router Layer

### 📄 `app/routers/webhook.py`
**Peran Utama:** Menangani endpoint Fonnte, validasi shared secret, otorisasi pengirim, dispatch perintah, balasan, setup sheet, dan health check webhook.

**Prinsip SOLID yang Dijaga:** Router menjadi entry point/use-case dispatcher dan mendelegasikan pekerjaan domain ke service. Namun, orkestrasi di `process_webhook_payload` cukup luas sehingga batas controller dan application service belum sepenuhnya tipis.

**Dependensi (Import):** Standard library `re`, `urllib.parse.parse_qs`; package eksternal `fastapi.APIRouter`, `Request`, `HTTPException`; `app.config.config`; `app.core.whatsapp.send_whatsapp`; `sales_service`, `karyawan_service`, `payroll_service`, `backup_service`, `webhook_state`; `app.services.setup_service.init_all_sheets`.

**Fungsi/Class Publik:**
- `router: APIRouter`: Memuat route `POST /webhook`, `GET /webhook`, dan `GET /setup`.
- `process_webhook_payload(data: dict) -> dict`: Memvalidasi secret, event ID, sender, rate limit, dan menjalankan perintah bisnis sesuai kategori pesan.

**Catatan Arsitektur:** Handler menerima variasi field payload dari JSON/form/query. Pengirim admin ditentukan service karyawan; nomor karyawan dikenali dari master data. Event dedupe hanya tersedia bila payload memiliki ID. Sebagian pemanggilan pengiriman WhatsApp tidak memeriksa boolean hasil kirim. `GET /setup` menjalankan perubahan spreadsheet dan tidak memiliki pemeriksaan autentikasi tersendiri pada kode router.

### 📄 `app/routers/report_router.py`
**Peran Utama:** Memvalidasi parameter bulan dan mengekspos use-case laporan mingguan/bulanan melalui HTTP.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada adaptasi HTTP; agregasi serta penulisan laporan didelegasikan ke `report_service`.

**Dependensi (Import):** Standard library `re`; package eksternal `fastapi.APIRouter`, `HTTPException`, `Query`; `app.services.report_service`.

**Fungsi/Class Publik:**
- `router: APIRouter`: Mendefinisikan `GET /report/mingguan` dan `GET /report/bulanan`.
- `report_mingguan(bulan: str) -> dict`: Memvalidasi format bulan dan menjalankan rekap mingguan tersimpan.
- `report_bulanan(bulan: str) -> dict`: Memvalidasi format bulan dan menjalankan rekap bulanan tersimpan.

**Catatan Arsitektur:** Walau menggunakan HTTP `GET`, service yang dipanggil menghapus/mengganti baris rekap di Sheets. Route tidak menunjukkan autentikasi/otorisasi. Route ini terpasang pada FastAPI, tetapi dispatcher WSGI PythonAnywhere saat ini tidak memetakannya.

## File Pendukung di Luar Lima Layer

### 📄 `app/read_sheets.py`
**Peran Utama:** Menyediakan CLI inspeksi metadata worksheet dan header spreadsheet tanpa membaca isi transaksi.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada pemeriksaan struktur secara read-only.

**Dependensi (Import):** Standard library `argparse`, `sys`; `app.repositories.sheet_repo`.

**Fungsi/Class Publik:**
- `read_sheets(sheet_name: str | None = None) -> int`: Mencetak metadata worksheet dan header semua sheet atau sheet terpilih.
- `main(argv: list[str] | None = None) -> int`: Membaca opsi CLI, memanggil inspeksi, dan menerjemahkan error menjadi exit code.

**Catatan Arsitektur:** Alat operasional untuk pemeriksaan schema. Ia membaca worksheet metadata/header yang diperlukan dan tidak mencetak semua baris transaksi.

### 📄 `app/setup_reference_structure.py`
**Peran Utama:** Mendefinisikan schema worksheet acuan serta memeriksa atau menerapkan pembuatan/standardisasi header.

**Prinsip SOLID yang Dijaga:** Single Responsibility pada schema reference dan operasi setup tingkat rendah; formatting setup lanjutan dilakukan `setup_service`.

**Dependensi (Import):** Standard library `argparse`, `re`, `sys`, `datetime.datetime`; package eksternal `gspread.exceptions.WorksheetNotFound`; `app.core.utils.parse_amount`; `app.repositories.sheet_repo`.

**Fungsi/Class Publik:**
- `SPECS`: Daftar nama worksheet, header, judul, dan metadata pembekuan baris.
- `LEDGER_HEADERS`: Header standar ledger unit.
- `setup_reference_structure(apply: bool = False, sheet_name: str | None = None) -> int`: Memeriksa schema (dry-run secara default) atau menerapkan struktur worksheet.
- `main(argv: list[str] | None = None) -> int`: Menyediakan CLI dengan opsi `--apply` dan pembatas `--sheet`.

**Catatan Arsitektur:** Dry-run adalah default. Saat apply, kode berusaha mempertahankan data legacy dan melewati struktur yang tidak cocok; tetap lakukan pemeriksaan spreadsheet/backup sebelum perubahan struktur penting.

### 📄 `app/migrate_tx_id.py`
**Peran Utama:** CLI dry-run/apply untuk menambahkan header `Transaction ID` di ujung schema dan mengisi ID deterministik pada transaksi legacy tanpa ID.

**Dependensi (Import):** Standard library `argparse`, `sys`; `app.config.config`; `app.core.transaction_id`; `app.core.utils.parse_amount`; `app.repositories.sheet_repo`.

**Fungsi Publik:**
- `migrate_transaction_ids(apply: bool = False) -> dict`: Menghitung baris yang perlu diisi atau menulis header dan ID; ID yang sudah ada tidak diubah.
- `main(argv: list[str] | None = None) -> int`: CLI `python -m app.migrate_tx_id`; dry-run default, `--apply` untuk menulis.

**Catatan Arsitektur:** Targetnya seluruh ledger unit, `(2)Rincian Transaksi Harian`, dan `_ArsipTransaksi`. Untuk data lama sender asli tidak tersedia, sehingga ID migrasi dibentuk dari field baris dengan sender kosong. Penulisan ID dibatch per worksheet untuk mengurangi request API. Baris dengan tanggal kosong/tidak valid dilewati dan jumlahnya ditampilkan per worksheet.

### 📄 `app/migrate_periodic_totals.py`
**Peran Utama:** CLI untuk melakukan migrasi atau backfill subtotal mingguan dan total bulanan secara manual.

**Dependensi (Import):** Standard library `argparse`, `logging`, `sys`, `datetime`; `app.config.config`; `app.core.utils.now_tz`; `app.services.periodic_total_service`.

**Fungsi Publik:**
- `main()`: Entry point CLI yang menerima parameter `--apply`, `--month`, dan `--all-units`.

**Catatan Arsitektur:** Digunakan sebagai recovery tool jika lazy trigger terlewat atau untuk inisialisasi data lama. Bersifat idempoten (mengganti baris subtotal yang sudah ada).

### 📄 `app/verify_archive.py`
**Peran Utama:** CLI untuk memeriksa ulang manifest arsip atau merencanakan/melakukan restore ke ledger.

**Dependensi (Import):** Standard library `argparse`, `sys`; `app.repositories.sheet_repo`.

**Fungsi Publik:**
- `main(argv: list[str] | None = None) -> int`: Menerima `--manifest <key>` untuk verifikasi atau `--restore <key>` untuk dry-run restore; `--apply` mengaktifkan penulisan restore.

**Catatan Arsitektur:** Restore hanya dilakukan dari manifest yang lolos verifikasi dan hanya menambahkan Transaction ID yang belum ada di ledger.
