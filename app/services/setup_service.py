import re

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


def _format_karyawan_sheets():
    header_style = {
        'backgroundColor': {'red': 0.12, 'green': 0.22, 'blue': 0.39},
        'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}, 'bold': True},
        'verticalAlignment': 'MIDDLE',
        'wrapStrategy': 'WRAP',
    }
    widths = {
        'Database Karyawan': [80, 180, 140, 140, 110, 130, 140, 250, 90, 120, 110, 110, 100, 100, 200],
        'Presensi Karyawan': [110, 70, 180, 90, 90, 90, 200, 100],
        'Slip Gaji': [80, 180, 100, 80, 90, 90, 70, 70, 130, 110, 130, 130, 140, 150, 100, 110, 130],
        'Penyesuaian Gaji': [110, 80, 180, 100, 100, 130, 220, 140],
    }
    ranges = {
        'Database Karyawan': 'A1:O1',
        'Presensi Karyawan': 'A1:H1',
        'Slip Gaji': 'A1:Q1',
        'Penyesuaian Gaji': 'A1:H1',
    }
    for name, header_range in ranges.items():
        worksheet = sheet_repo.get_sheet(name)
        worksheet.freeze(rows=1)
        worksheet.format(header_range, header_style)
        requests = []
        for column, width in enumerate(widths[name]):
            requests.append({
                'updateDimensionProperties': {
                    'range': {
                        'sheetId': worksheet.id,
                        'dimension': 'COLUMNS',
                        'startIndex': column,
                        'endIndex': column + 1,
                    },
                    'properties': {'pixelSize': width},
                    'fields': 'pixelSize',
                }
            })
        worksheet.spreadsheet.batch_update({'requests': requests})

    sheet_repo.get_sheet('Database Karyawan').format('A:A', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Database Karyawan').format('G:G', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Database Karyawan').format('E2:E', {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}})
    sheet_repo.get_sheet('Database Karyawan').format('F2:F', {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}})
    sheet_repo.get_sheet('Database Karyawan').format('K2:K', {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}})
    sheet_repo.get_sheet('Presensi Karyawan').format('A2:A', {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}})
    sheet_repo.get_sheet('Presensi Karyawan').format('B:B', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Presensi Karyawan').format('D:E', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Slip Gaji').format('A:A', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Slip Gaji').format('C:C', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Slip Gaji').format('I2:M', {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}})
    sheet_repo.get_sheet('Slip Gaji').format('N2:N', {'numberFormat': {'type': 'DATE_TIME', 'pattern': 'yyyy-mm-dd hh:mm'}})
    sheet_repo.get_sheet('Slip Gaji').format('O2:Q', {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}})
    sheet_repo.get_sheet('Penyesuaian Gaji').format('A2:A', {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}})
    sheet_repo.get_sheet('Penyesuaian Gaji').format('B:B', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Penyesuaian Gaji').format('D:D', {'numberFormat': {'type': 'TEXT'}})
    sheet_repo.get_sheet('Penyesuaian Gaji').format('F2:F', {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}})

    validation_ranges = [
        ('Database Karyawan', 'I2:I', 'one_of_list', ['Aktif', 'Nonaktif']),
        ('Presensi Karyawan', 'F2:F', 'one_of_list', ['Hadir', 'Izin', 'Sakit', 'Cuti', 'Alpha']),
        ('Penyesuaian Gaji', 'E2:E', 'one_of_list', ['Bonus', 'Potongan']),
    ]
    for name, cell_range, condition, values in validation_ranges:
        worksheet = sheet_repo.get_sheet(name)
        match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)', cell_range)
        if not match:
            continue
        start_col = 0
        for character in match.group(1):
            start_col = start_col * 26 + ord(character) - ord('A') + 1
        start_col -= 1
        worksheet.spreadsheet.batch_update({'requests': [{
            'setDataValidation': {
                'range': {
                    'sheetId': worksheet.id,
                    'startRowIndex': int(match.group(2)) - 1,
                    'endRowIndex': 1000,
                    'startColumnIndex': start_col,
                    'endColumnIndex': start_col + 1,
                },
                'rule': {
                    'condition': {
                        'type': 'ONE_OF_LIST',
                        'values': [{'userEnteredValue': value} for value in values],
                    },
                    'strict': True,
                    'showCustomUi': True,
                },
            }
        }]})

    recap_widths = [120, 220, 110, 100, 140, 140]
    for recap_name in ('_RekapHarian', '_RekapBulanan'):
        worksheet = sheet_repo.get_sheet(recap_name)
        worksheet.freeze(rows=1)
        worksheet.format('A1:F1', header_style)
        worksheet.format('A2:A', {'numberFormat': {'type': 'TEXT'}})
        worksheet.format('C2:C', {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0.##'}})
        worksheet.format('E2:E', {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}})
        for column, width in enumerate(recap_widths):
            worksheet.spreadsheet.batch_update({'requests': [{
                'updateDimensionProperties': {
                    'range': {
                        'sheetId': worksheet.id,
                        'dimension': 'COLUMNS',
                        'startIndex': column,
                        'endIndex': column + 1,
                    },
                    'properties': {'pixelSize': width},
                    'fields': 'pixelSize',
                }
            }]})

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
        log_headers = ['Timestamp', 'Sender', 'Raw Message', 'Status', 'Faktur', 'Error']
        log_values = logs.get_all_values()
        if not log_values:
            logs.append_row(log_headers)
        elif len(log_values[0]) < len(log_headers) or log_values[0][:len(log_headers)] != log_headers:
            logs.update('A1:F1', [log_headers])
        meta = sheet_repo.get_sheet("_Meta")
        if not meta.get_all_values():
            meta.append_row(['Key', 'Value'])
            meta.append_row(['INV_COUNTER', '0'])
        pending = sheet_repo.get_sheet("_PendingConfirm")
        if not pending.get_all_values():
            pending.append_row(['Sender', 'Message', 'ExpiresAt'])
        daily_recap = sheet_repo.get_sheet("_RekapHarian")
        if not daily_recap.get_all_values():
            daily_recap.append_row(['Tanggal', 'Produk', 'Kuantitas', 'Satuan', 'Total (Rp)', 'Jumlah Transaksi'])
        monthly_recap = sheet_repo.get_sheet("_RekapBulanan")
        if not monthly_recap.get_all_values():
            monthly_recap.append_row(['Bulan', 'Produk', 'Kuantitas', 'Satuan', 'Total (Rp)', 'Jumlah Transaksi'])
        print("✅ Sheet _Logs, _Meta, _PendingConfirm & rekap siap.")
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
        gaji = sheet_repo.get_sheet("Slip Gaji")
        if not gaji.get_all_values():
            gaji.append_row(['NIK', 'Nama', 'Periode', 'Hadir', 'Terlambat', 'Izin/Sakit', 'Cuti', 'Alpha', 'Gaji Pokok', 'Tunjangan', 'Potongan Alpha', 'Potongan Telat', 'Gaji Bersih', 'Dicetak', 'Lembur', 'Bonus', 'Potongan Lain'])
        adj = sheet_repo.get_sheet("Penyesuaian Gaji")
        if not adj.get_all_values():
            adj.append_row(['Tanggal', 'NIK', 'Nama', 'Periode', 'Jenis', 'Jumlah (Rp)', 'Keterangan', 'Dicatat Oleh'])
        _format_karyawan_sheets()
        print("✅ Sheet Database Karyawan, Presensi, Slip Gaji & Penyesuaian siap.")
    except Exception as e:
        print(f"❌ Gagal setup Database Karyawan: {e}")

    print("🎉 SEMUA STRUKTUR MULTI-UNIT SIAP DIGUNAKAN!")