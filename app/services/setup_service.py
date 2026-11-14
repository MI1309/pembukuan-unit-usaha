import re

from app.repositories import sheet_repo
from app.config import config
from app.setup_reference_structure import setup_reference_structure

def setup_sheet_unit(sheet_name):
    """Create or format a unit ledger using the shared 9-column layout."""
    sheet = sheet_repo.get_sheet(sheet_name)
    headers = [
        'Nomor', 'Tanggal', 'Nama Barang/Jasa /Produk', 'Keterangan Transaksi',
        'Kuantitas', 'Satuan', 'Pemasukan (Rp)', 'Pengeluaran (Rp)', 'Sisa Saldo',
    ]
    values = sheet.get_all_values()
    if not values:
        sheet.update('A1:I2', [[f"Kakorlap Unit Usaha {sheet_name}", '', '', '', '', '', '', '', ''], headers])
        sheet.merge_cells('A1:I1')
        print(f"✅ Sheet Unit '{sheet_name}' dibuat.")
    else:
        title = str(values[0][0]).strip() if values[0] else ''
        if title.lower().startswith('kakorlap unit usaha') and len(values) > 1:
            sheet.update('A2:I2', [headers])
        print(f"ℹ️ Sheet Unit '{sheet_name}' sudah ada.")

    requests = [
        {'repeatCell': {
            'range': {'sheetId': sheet.id, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 9},
            'cell': {'userEnteredFormat': {
                'backgroundColor': {'red': 0.12, 'green': 0.22, 'blue': 0.39},
                'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}, 'bold': True, 'fontSize': 14},
                'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE',
            }},
            'fields': 'userEnteredFormat',
        }},
        {'repeatCell': {
            'range': {'sheetId': sheet.id, 'startRowIndex': 1, 'endRowIndex': 2, 'startColumnIndex': 0, 'endColumnIndex': 9},
            'cell': {'userEnteredFormat': {
                'backgroundColor': {'red': 0.12, 'green': 0.22, 'blue': 0.39},
                'textFormat': {'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}, 'bold': True},
                'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE', 'wrapStrategy': 'WRAP',
            }},
            'fields': 'userEnteredFormat',
        }},
        {'repeatCell': {
            'range': {'sheetId': sheet.id, 'startRowIndex': 1, 'startColumnIndex': 1, 'endColumnIndex': 2},
            'cell': {'userEnteredFormat': {'numberFormat': {'type': 'DATE', 'pattern': 'dd mmm yyyy'}}},
            'fields': 'userEnteredFormat.numberFormat',
        }},
        {'repeatCell': {
            'range': {'sheetId': sheet.id, 'startRowIndex': 1, 'startColumnIndex': 5, 'endColumnIndex': 9},
            'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}}},
            'fields': 'userEnteredFormat.numberFormat',
        }},
        {'updateSheetProperties': {
            'properties': {'sheetId': sheet.id, 'gridProperties': {'frozenRowCount': 2}},
            'fields': 'gridProperties.frozenRowCount',
        }},
    ]
    for row_number, row in enumerate(values, start=1):
        if any(str(value).strip().lower() == 'jumlah' for value in row):
            requests.append({'repeatCell': {
                'range': {'sheetId': sheet.id, 'startRowIndex': row_number - 1, 'endRowIndex': row_number, 'startColumnIndex': 0, 'endColumnIndex': 9},
                'cell': {'userEnteredFormat': {
                    'backgroundColor': {'red': 0.91, 'green': 0.65, 'blue': 0.0},
                    'textFormat': {'bold': True},
                }},
                'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat',
            }})
    widths = [110, 125, 230, 290, 115, 105, 145, 145, 145]
    requests.extend({
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet.id,
                'dimension': 'COLUMNS',
                'startIndex': column,
                'endIndex': column + 1,
            },
            'properties': {'pixelSize': width},
            'fields': 'pixelSize',
        }
    } for column, width in enumerate(widths))
    sheet.spreadsheet.batch_update({'requests': requests})


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
    worksheets = {name: sheet_repo.get_sheet(name) for name in ranges}
    spreadsheet = next(iter(worksheets.values())).spreadsheet
    requests = []
    for name, worksheet in worksheets.items():
        column_count = len(widths[name])
        requests.extend([
            {'updateSheetProperties': {
                'properties': {'sheetId': worksheet.id, 'gridProperties': {'frozenRowCount': 1}},
                'fields': 'gridProperties.frozenRowCount',
            }},
            {'repeatCell': {
                'range': {'sheetId': worksheet.id, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': column_count},
                'cell': {'userEnteredFormat': header_style},
                'fields': 'userEnteredFormat',
            }},
        ])
        requests.extend({
            'updateDimensionProperties': {
                'range': {'sheetId': worksheet.id, 'dimension': 'COLUMNS', 'startIndex': column, 'endIndex': column + 1},
                'properties': {'pixelSize': width},
                'fields': 'pixelSize',
            }
        } for column, width in enumerate(widths[name]))

    number_formats = [
        ('Database Karyawan', 0, 1, 0, None, {'type': 'TEXT'}),
        ('Database Karyawan', 6, 7, 0, None, {'type': 'TEXT'}),
        ('Database Karyawan', 4, 5, 1, None, {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}),
        ('Database Karyawan', 5, 6, 1, None, {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}),
        ('Database Karyawan', 10, 11, 1, None, {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}),
        ('Presensi Karyawan', 0, 1, 1, None, {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}),
        ('Presensi Karyawan', 1, 2, 0, None, {'type': 'TEXT'}),
        ('Presensi Karyawan', 3, 5, 0, None, {'type': 'TEXT'}),
        ('Slip Gaji', 0, 1, 0, None, {'type': 'TEXT'}),
        ('Slip Gaji', 2, 3, 0, None, {'type': 'TEXT'}),
        ('Slip Gaji', 8, 13, 1, None, {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}),
        ('Slip Gaji', 13, 14, 1, None, {'type': 'DATE_TIME', 'pattern': 'yyyy-mm-dd hh:mm'}),
        ('Slip Gaji', 14, 17, 1, None, {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}),
        ('Penyesuaian Gaji', 0, 1, 1, None, {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}),
        ('Penyesuaian Gaji', 1, 2, 0, None, {'type': 'TEXT'}),
        ('Penyesuaian Gaji', 3, 4, 0, None, {'type': 'TEXT'}),
        ('Penyesuaian Gaji', 5, 6, 1, None, {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}),
    ]
    for name, start_column, end_column, start_row, end_row, number_format in number_formats:
        worksheet = worksheets[name]
        cell_range = {
            'sheetId': worksheet.id,
            'startRowIndex': start_row,
            'startColumnIndex': start_column,
            'endColumnIndex': end_column,
        }
        if end_row is not None:
            cell_range['endRowIndex'] = end_row
        requests.append({'repeatCell': {
            'range': cell_range,
            'cell': {'userEnteredFormat': {'numberFormat': number_format}},
            'fields': 'userEnteredFormat.numberFormat',
        }})

    validation_ranges = [
        ('Database Karyawan', 'I2:I', 'one_of_list', ['Aktif', 'Nonaktif']),
        ('Presensi Karyawan', 'F2:F', 'one_of_list', ['Hadir', 'Izin', 'Sakit', 'Cuti', 'Alpha']),
        ('Penyesuaian Gaji', 'E2:E', 'one_of_list', ['Bonus', 'Potongan']),
    ]
    for name, cell_range, condition, values in validation_ranges:
        match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)', cell_range)
        if not match:
            continue
        start_col = 0
        for character in match.group(1):
            start_col = start_col * 26 + ord(character) - ord('A') + 1
        start_col -= 1
        worksheet = worksheets[name]
        requests.append({
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
        })

    recap_widths = [120, 220, 110, 100, 140, 140]
    for recap_name in ('_RekapHarian', '(1)Rekap Bulanan '):
        worksheet = sheet_repo.get_sheet(recap_name)
        requests.extend([
            {'updateSheetProperties': {
                'properties': {'sheetId': worksheet.id, 'gridProperties': {'frozenRowCount': 1}},
                'fields': 'gridProperties.frozenRowCount',
            }},
            {'repeatCell': {
                'range': {'sheetId': worksheet.id, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 6},
                'cell': {'userEnteredFormat': header_style},
                'fields': 'userEnteredFormat',
            }},
            {'repeatCell': {
                'range': {'sheetId': worksheet.id, 'startRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 1},
                'cell': {'userEnteredFormat': {'numberFormat': {'type': 'TEXT'}}},
                'fields': 'userEnteredFormat.numberFormat',
            }},
            {'repeatCell': {
                'range': {'sheetId': worksheet.id, 'startRowIndex': 1, 'startColumnIndex': 2, 'endColumnIndex': 3},
                'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0.##'}}},
                'fields': 'userEnteredFormat.numberFormat',
            }},
            {'repeatCell': {
                'range': {'sheetId': worksheet.id, 'startRowIndex': 1, 'startColumnIndex': 4, 'endColumnIndex': 5},
                'cell': {'userEnteredFormat': {'numberFormat': {'type': 'CURRENCY', 'pattern': '"Rp" #,##0'}}},
                'fields': 'userEnteredFormat.numberFormat',
            }},
        ])
        requests.extend({
            'updateDimensionProperties': {
                'range': {'sheetId': worksheet.id, 'dimension': 'COLUMNS', 'startIndex': column, 'endIndex': column + 1},
                'properties': {'pixelSize': width},
                'fields': 'pixelSize',
            }
        } for column, width in enumerate(recap_widths))

    spreadsheet.batch_update({'requests': requests})

def init_all_sheets():
    print("⏳ Memulai inisialisasi struktur Multi-Unit...")
    setup_status = setup_reference_structure(apply=True)
    if setup_status:
        print("❌ Setup struktur spreadsheet belum lengkap; periksa pesan di atas.")
        return False

    for unit in config.UNIT_USAHA:
        setup_sheet_unit(unit)

    meta = sheet_repo.get_sheet("_Meta", create=False)
    meta_values = meta.get_all_values()
    if not any(row and row[0].strip() == "INV_COUNTER" for row in meta_values[1:]):
        sheet_repo.append_row("_Meta", ["INV_COUNTER", "0"])

    _format_karyawan_sheets()
    print("🎉 SEMUA STRUKTUR MULTI-UNIT SIAP DIGUNAKAN!")
    return True