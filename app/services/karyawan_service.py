import math
from datetime import datetime
from app.repositories import sheet_repo
from app.core.utils import now_tz, format_tanggal_id, format_rupiah, parse_amount
from app.config import config

def get_karyawan(nik: str) -> dict:
    data = sheet_repo.get_all_records("Database Karyawan")
    for k in data:
        if str(k.get('NIK')) == str(nik): return k
    return None

def handle_karyawan(message: str, sender: str) -> str:
    msg = message.lower().strip()
    now = now_tz()
    
    # 1. Presensi Masuk/Pulang (Sederhana)
    if msg in ["masuk", "pulang"]:
        status = "Hadir" if msg == "masuk" else "Pulang"
        jam = now.strftime("%H:%M")
        sheet_repo.append_row("Presensi Karyawan", [now.strftime("%Y-%m-%d"), "001", "Budi", jam, "-", status, "-", 0])
        return f"✅ {status} tercatat pukul {jam}."
        
    # 2. Cek Data Karyawan
    if msg.startswith("cek karyawan"):
        nik = msg.split(" ")[-1]
        k = get_karyawan(nik)
        if k: return f"👤 *{k['Nama Karyawan']}*\nJabatan: {k['Jabatan']}\nGaji: {format_rupiah(k['Gaji Pokok'])}"
        return "❌ NIK tidak ditemukan."

    # 3. Slip Gaji
    if msg == "slip gaji":
        return f"💵 *SLIP GAJI*\nPeriode: {config.BULAN[now.month-1]} {now.year}\nGaji Pokok: {format_rupiah(8000000)}\nPotongan: 0\n*Gaji Bersih: {format_rupiah(8000000)}*"
        
    return None