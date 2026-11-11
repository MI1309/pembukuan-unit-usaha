import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _project_path(name: str, default_name: str) -> str:
    value = Path(os.getenv(name, default_name)).expanduser()
    return str(value if value.is_absolute() else PROJECT_ROOT / value)

class Config:
    # Kredensial
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
    GOOGLE_CREDENTIALS_FILE = _project_path("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
    
    # Endpoint API
    FONNTE_URL = "https://api.fonnte.com/send"
    GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    GROQ_MODEL = "qwen/qwen3.8-27b"
    
    # Aturan Bisnis
    TIMEZONE = "Asia/Jakarta"
    COMPANY = "PT Lentera Agrinova Organik"
    ALLOWED_SENDERS = ['6285230495385', '6281803447100', '6289514366445']
    ADMIN_SENDERS = [] # Kosongkan jika sama dengan ALLOWED_SENDERS
    ADMIN_NOTIFY = '6289514366445'  # Nomor WA untuk notifikasi admin (opsional)
    CONFIRM_THRESHOLD = 1000000000
    CONFIRM_TTL_SECONDS = 300
    RATE_LIMIT_PER_MINUTE = 10
    WEBHOOK_DEDUPE_TTL_SECONDS = 180

    BACKUP_ENABLED = True
    BACKUP_FOLDER_NAME = "Agrinova_Backup"
    BACKUP_FOLDER_ID = os.getenv("BACKUP_FOLDER_ID")
    DRIVE_OAUTH_CLIENT_FILE = _project_path("DRIVE_OAUTH_CLIENT_FILE", "oauth_client.json")
    DRIVE_OAUTH_TOKEN_FILE = _project_path("DRIVE_OAUTH_TOKEN_FILE", "drive_token.json")
    BACKUP_KEEP_DAYS = 30
    BACKUP_HOUR = 1
    
    # Karyawan & Gaji
    GAJI_HARI_KERJA = 26
    TUNJANGAN_PERSEN = 10
    POTONGAN_TELAT = 25000
    JAM_MASUK = "08:00"
    JAM_KELUAR = "17:00"
    LEMBUR_PER_JAM = 0
    TOLERANSI_TELAT_MENIT = 0
    HARI_LIBUR_MINGGUAN = [6]  # Python weekday: Monday=0, Sunday=6
    HARI_LIBUR = []
    AUTO_ALPHA = True
    SHEET_KARYAWAN = "Database Karyawan"
    SHEET_PRESENSI = "Presensi Karyawan"
    SHEET_GAJI = "Slip Gaji"
    SHEET_PENYESUAIAN_GAJI = "Penyesuaian Gaji"
    
    # Katalog Penjualan
    KATALOG = [
        {"kunci": ["telur"],  "nama": "Telur Ayam Fazza",       "kategori": "telur",  "satuan": "BUTIR"},
        {"kunci": ["anggur"], "nama": "Anggur siap jual",       "kategori": "ANGGUR", "satuan": "KG"},
        {"kunci": ["lele"],   "nama": "Lele Fazza Siap Jual",   "kategori": "LELE",   "satuan": "EKOR"},
        {"kunci": ["ayam"],   "nama": "Jual Ayam Jantan Fazza", "kategori": "AYAM",   "satuan": "EKOR"}
    ]
    
    BULAN = ['Januari','Februari','Maret','April','Mei','Juni','Juli','Agustus','September','Oktober','November','Desember']
    
    # Daftar Unit Usaha (Sesuaikan dengan tab di Spreadsheet kamu)
    UNIT_USAHA = [
        "(3)Pertanian ",
        "(4)Ternak Ayam Fazza",
        "(5)Ternak Ayam Emran",
        "(6)Perikanan ",
        "(7)Pabrik Pupuk",
    ]

config = Config()