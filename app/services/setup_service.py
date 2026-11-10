import gspread
from app.repositories import sheet_repo
from app.config import config

def setup_sheet_unit(sheet_name):
    """Membuat sheet unit dengan format 9 kolom (Pemasukan/Pengeluaran/Saldo)"""
    sheet = sheet_repo.get_sheet(sheet_name)
    
    # Cek jika sheet baru dibuat (kosong)
    if not sheet.get_all_values():
        # Baris 1: Judul
        sheet.update('A1:I1', [[f"Kakorlap Unit Usaha {sheet_name}"]])
        sheet.merge_cells('A1:I1')
        sheet.format('A1:I1', {'horizontalAlignment': 'CENTER', 'textFormat': {'bold': True, 'fontSize': 14}})
        
        # Baris 2: Header
        headers = ['Nomor', 'Tanggal', 'Nama Barang/Jasa /Produk', 'Keterangan Transaksi', 'Kuantitas', 'Satuan (Rp)', 'Pemasukan (Rp)', 'Pengeluaran (Rp)', 'Saldo']
        sheet.append_row(headers)
        sheet.format('A2:I2', {'backgroundColor': {'red': 0.12, 'green': 0.22, 'blue': 0.39}, 'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}, 'bold': True}})
        
        # Format Kolom
        sheet.format('B:B', {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}})
        sheet.format('G:I', {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}})
        
        print(f"✅ Sheet Unit '{sheet_name}' dibuat.")
    else:
        print(f"ℹ️ Sheet Unit '{sheet_name}' sudah ada.")

def init_all_sheets():
    print("⏳ Memulai inisialisasi struktur Multi-Unit...")
    
    # 1. Setup semua Unit Usaha
    for unit in config.UNIT_USAHA:
        setup_sheet_unit(unit)

    # 2. Setup Sheet Arsip Transaksi (Rollover)
    try:
        arsip = sheet_repo.get_sheet("_ArsipTransaksi")
        if not arsip.get_all_values():
            headers = ['Unit Usaha', 'Nomor', 'Tanggal', 'Nama Barang/Jasa/Produk', 'Keterangan Transaksi', 'Kuantitas', 'Satuan', 'Pemasukan (Rp)', 'Pengeluaran (Rp)', 'Saldo', 'Diarsipkan']
            arsip.append_row(headers)
            print("✅ Sheet _ArsipTransaksi dibuat.")
    except Exception as e:
        print(f"❌ Gagal setup _ArsipTransaksi: {e}")

    # 3. Setup Sheet Logs & Meta
    try:
        logs = sheet_repo.get_sheet("_Logs")
        if not logs.get_all_values(): logs.append_row(['Timestamp', 'Sender', 'Status', 'Error'])
        meta = sheet_repo.get_sheet("_Meta")
        if not meta.get_all_values():
            meta.append_row(['Key', 'Value'])
            meta.append_row(['INV_COUNTER', '0'])
        print("✅ Sheet _Logs & _Meta siap.")
    except Exception as e:
        print(f"❌ Gagal setup _Logs & _Meta: {e}")

    # 4. Setup Database Karyawan & Presensi (Untuk Modul 2 nanti)
    try:
        kar = sheet_repo.get_sheet("Database Karyawan")
        if not kar.get_all_values():
            kar.append_row(['NIK', 'Nama Karyawan', 'Jabatan', 'Departemen', 'Tanggal Masuk', 'Gaji Pokok', 'No. HP', 'Alamat', 'Status', 'Tempat Lahir', 'Tanggal Lahir', 'Jenis Kelamin', 'Status Nikah', 'Pendidikan', 'Pengalaman Kerja'])
        
        pres = sheet_repo.get_sheet("Presensi Karyawan")
        if not pres.get_all_values():
            pres.append_row(['Tanggal', 'NIK', 'Nama Karyawan', 'Jam Masuk', 'Jam Keluar', 'Status', 'Keterangan', 'Lembur (jam)'])
        print("✅ Sheet Database Karyawan & Presensi siap.")
    except Exception as e:
        print(f"❌ Gagal setup Database Karyawan: {e}")

    print("🎉 SEMUA STRUKTUR MULTI-UNIT SIAP DIGUNAKAN!")