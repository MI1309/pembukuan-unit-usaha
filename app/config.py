import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Kredensial
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
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
    ADMIN_NOTIFY = '6281234567890'
    CONFIRM_THRESHOLD = 1000000000
    
    # Karyawan & Gaji
    GAJI_HARI_KERJA = 26
    TUNJANGAN_PERSEN = 10
    POTONGAN_TELAT = 25000
    JAM_MASUK = "08:00"
    JAM_KELUAR = "17:00"
    LEMBUR_PER_JAM = 0
    
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
        "Perikanan Emran",
        "Ternak Ayam Fazza",
        "Ternak Ayam Emran",
        "Pertanian Anggur",
        "Pertanian Jagung",
        "Pertanian Padi",
        "Pertanian Melon",
        "Pabrik Pupuk"
    ]

config = Config()