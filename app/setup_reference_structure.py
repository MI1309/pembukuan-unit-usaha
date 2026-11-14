import argparse
import re
import sys
from datetime import datetime

from gspread.exceptions import WorksheetNotFound

from app.core.utils import parse_amount
from app.repositories import sheet_repo


LEDGER_HEADERS = [
    "Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi",
    "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo",
]
SPECS = [
    {"name": "(1)Rekap Bulanan ", "headers": ["Bulan", "Produk/Jasa/Barang", "Kuantitas", "Satuan", "Total (Rp)", "Jumlah Transaksi"], "freeze": 1},
    {"name": "(2)Rincian Transaksi Harian", "title": "Industri Pertanian Terpadu Lentera Farm", "headers": ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)"], "header_row": 5, "freeze": 5},
    {"name": "(3)Pertanian ", "title": "Kakorlap Unit Usaha Pertanian", "headers": LEDGER_HEADERS, "freeze": 2},
    {"name": "(4)Ternak Ayam Fazza", "title": "Kakorlap Unit Usaha Ternak Ayam Fazza : Agus Adip", "headers": LEDGER_HEADERS, "freeze": 2},
    {"name": "(5)Ternak Ayam Emran", "title": "Kakorlap Unit Usaha Ternak Ayam Emran: Agus Adip", "headers": LEDGER_HEADERS, "freeze": 2},
    {"name": "(6)Perikanan ", "title": "Kakorlap Unit Usaha Perikanan Emran: Jatmiko", "headers": LEDGER_HEADERS, "freeze": 2},
    {"name": "(7)Pabrik Pupuk", "title": "Kakorlap Unit Usaha Pabrik Pupuk ( Widi )", "headers": LEDGER_HEADERS, "freeze": 2},
    {"name": "_Logs", "headers": ["Timestamp", "Sender", "Raw Message", "Status", "Faktur", "Error"], "freeze": 1},
    {"name": "_Meta", "headers": ["Key", "Value"], "freeze": 1},
    {"name": "_PendingConfirm", "headers": ["Sender", "Message", "ExpiresAt"], "freeze": 1},
    {"name": "_RekapHarian", "headers": ["Tanggal", "Produk", "Kuantitas", "Satuan", "Total (Rp)", "Jumlah Transaksi"], "freeze": 1},
    {"name": "_ArsipTransaksi", "headers": ["Unit Usaha", "Nomor", "Tanggal", "Nama Barang/Jasa/Produk", "Keterangan", "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Saldo", "Diarsipkan"], "freeze": 1},
    {"name": "_ArsipRincianHarian", "headers": ["Bulan Arsip", "Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)"], "freeze": 1},
    {"name": "Database Karyawan", "headers": ["NIK", "Nama Karyawan", "Jabatan", "Departemen", "Tanggal Masuk", "Gaji Pokok", "No. HP", "Alamat", "Status", "Tempat Lahir", "Tanggal Lahir", "Jenis Kelamin", "Status Nikah", "Pendidikan", "Pengalaman Kerja"], "freeze": 1},
    {"name": "Presensi Karyawan", "headers": ["Tanggal", "NIK", "Nama Karyawan", "Jam Masuk", "Jam Keluar", "Status", "Keterangan", "Lembur (jam)"], "freeze": 1},
    {"name": "Slip Gaji", "headers": ["NIK", "Nama", "Periode", "Hadir", "Terlambat", "Izin/Sakit", "Cuti", "Alpha", "Gaji Pokok", "Tunjangan", "Potongan Alpha", "Potongan Telat", "Gaji Bersih", "Dicetak", "Lembur", "Bonus", "Potongan Lain"], "freeze": 1},
    {"name": "Penyesuaian Gaji", "headers": ["Tanggal", "NIK", "Nama", "Periode", "Jenis", "Jumlah (Rp)", "Keterangan", "Dicatat Oleh"], "freeze": 1},
    {"name": "Rincian Penjualan", "headers": ["Produk", "Nomor", "Tanggal", "Keterangan", "Kuantitas", "Satuan", "Total (Rp)"], "freeze": 1, "insert_legacy_header": True},
]
HEADER_STYLE = {
    "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
    "verticalAlignment": "MIDDLE",
    "wrapStrategy": "WRAP",
}
TITLE_STYLE = {
    "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 14},
    "horizontalAlignment": "CENTER",
    "verticalAlignment": "MIDDLE",
}


def _row_matches(row: list, headers: list[str]) -> bool:
    return [str(value).strip().lower() for value in row[:len(headers)]] == [header.lower() for header in headers]


def _column_name(column: int) -> str:
    name = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _looks_like_legacy_sale(row: list) -> bool:
    if len(row) < 7 or not str(row[1]).strip().upper().startswith("INV/"):
        return False
    if not str(row[0]).strip():
        return False
    try:
        quantity = float(str(row[4]).strip().replace(",", "."))
    except (TypeError, ValueError, IndexError):
        return False
    return bool(str(row[2]).strip()) and quantity > 0 and parse_amount(row[6]) > 0


def _worksheet(spreadsheet, name: str, apply: bool):
    try:
        return spreadsheet.worksheet(name), False
    except WorksheetNotFound:
        if not apply:
            return None, True
        return spreadsheet.add_worksheet(title=name, rows=1000, cols=max(26, len(LEDGER_HEADERS))), True


def _ensure_spec(spreadsheet, spec: dict, apply: bool) -> str:
    worksheet, missing = _worksheet(spreadsheet, spec["name"], apply)
    if missing and not apply:
        return "CREATE"

    headers = spec["headers"]
    header_row = spec.get("header_row", 2 if spec.get("title") else 1)
    end_column = _column_name(len(headers))
    values = worksheet.get(f"1:{header_row}")
    header_index = header_row - 1
    existing_header = values[header_index] if len(values) > header_index else []

    if missing:
        if spec.get("title"):
            worksheet.update(
                range_name=f"A1:{end_column}1",
                values=[[spec["title"], *( [""] * (len(headers) - 1))]],
            )
            worksheet.update(
                range_name=f"A{header_row}:{end_column}{header_row}",
                values=[headers],
            )
            worksheet.merge_cells(f"A1:{end_column}1")
        else:
            worksheet.update(range_name=f"A1:{end_column}1", values=[headers])
        action = "CREATED"
    elif _row_matches(existing_header, headers):
        action = "OK"
    elif not any(str(value).strip() for value in existing_header):
        if spec.get("title") and values and str(values[0][0]).strip():
            title = str(values[0][0]).strip()
            normalized_title = re.sub(r"\s+", " ", title).strip().lower()
            expected_title = re.sub(r"\s+", " ", spec["title"]).strip().lower()
            if normalized_title != expected_title:
                return "SKIP: row 1 is not the expected ledger title"
        if spec.get("insert_legacy_header") and values and _looks_like_legacy_sale(values[0]):
            if not apply:
                return "INSERT HEADER ABOVE EXISTING DATA"
            worksheet.insert_row(headers, index=1, value_input_option="RAW")
            action = "HEADER INSERTED; DATA PRESERVED"
        elif spec.get("title"):
            if not apply:
                return f"ADD HEADER AT ROW {header_row}"
            worksheet.update(range_name=f"A{header_row}:{end_column}{header_row}", values=[headers])
            action = "HEADER ADDED"
        else:
            if not apply:
                return "ADD HEADER AT ROW 1"
            worksheet.update(range_name=f"A1:{end_column}1", values=[headers])
            action = "HEADER ADDED"
    elif str(existing_header[0]).strip().lower() == headers[0].lower():
        if not apply:
            return "STANDARDIZE HEADER"
        worksheet.update(
            range_name=f"A{header_row}:{end_column}{header_row}",
            values=[headers],
        )
        action = "HEADER STANDARDIZED"
    elif (spec.get("insert_legacy_header") and values
          and any(str(value).strip() for value in values[0])):
        if not apply:
            return "INSERT HEADER ABOVE EXISTING ROW; DATA PRESERVED"
        worksheet.insert_row(headers, index=1, value_input_option="RAW")
        action = "HEADER INSERTED; DATA PRESERVED"
    else:
        return "SKIP: existing row does not match expected header; data preserved"

    if apply and action not in {"OK"}:
        worksheet.format(f"A{header_row}:{end_column}{header_row}", HEADER_STYLE)
        worksheet.freeze(rows=spec.get("freeze", 1))
        if spec.get("title"):
            worksheet.format(f"A1:{end_column}1", TITLE_STYLE)
    return action


def setup_reference_structure(apply: bool = False, sheet_name: str | None = None) -> int:
    spreadsheet = sheet_repo.get_spreadsheet()
    specs = [spec for spec in SPECS if sheet_name is None or spec["name"] == sheet_name]
    if sheet_name and not specs:
        print(f"Worksheet tidak ada di struktur referensi: {sheet_name}", file=sys.stderr)
        return 2

    print(f"Spreadsheet: {spreadsheet.title}")
    print("MODE: APPLY" if apply else "MODE: DRY RUN (tidak ada perubahan)")
    skipped = 0
    for spec in specs:
        try:
            result = _ensure_spec(spreadsheet, spec, apply)
        except Exception as error:
            result = f"ERROR: {error}"
        print(f"{spec['name']!r}: {result}")
        if result.startswith(("SKIP", "ERROR")):
            skipped += 1
    print(f"Selesai: {len(specs) - skipped} siap/direncanakan, {skipped} dilewati/error.")
    return 1 if skipped else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buat struktur tab/header berdasarkan struktur_utama.txt.")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan; tanpa opsi ini hanya dry run")
    parser.add_argument("--sheet", help="Batasi ke satu nama worksheet dari struktur referensi")
    args = parser.parse_args(argv)
    return setup_reference_structure(args.apply, args.sheet)


if __name__ == "__main__":
    raise SystemExit(main())
