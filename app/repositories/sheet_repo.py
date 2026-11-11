import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from threading import RLock
from gspread.utils import numericise
from time import monotonic

from app.config import config

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = None
client = None
_spreadsheet = None
_spreadsheet_lock = RLock()
_worksheet_cache = {}
_record_cache = {}
_cache_lock = RLock()
_READ_CACHE_SECONDS = 3
_SIMPLE_SHEET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_client():
    global creds, client
    if client is None:
        with _spreadsheet_lock:
            if client is None:
                creds = ServiceAccountCredentials.from_json_keyfile_name(config.GOOGLE_CREDENTIALS_FILE, scope)
                client = gspread.authorize(creds)
    return client


def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        with _spreadsheet_lock:
            if _spreadsheet is None:
                _spreadsheet = get_client().open_by_key(config.SPREADSHEET_ID)
    return _spreadsheet

def get_sheet(name: str, create=True):
    with _cache_lock:
        if name in _worksheet_cache:
            worksheet = _worksheet_cache[name]
            if worksheet is not None or not create:
                return worksheet

        spreadsheet = get_spreadsheet()
        try:
            worksheet = spreadsheet.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=name, rows=1000, cols=20) if create else None
        if worksheet is not None:
            _worksheet_cache[name] = worksheet
        return worksheet


def invalidate_cache(sheet_name: str) -> None:
    with _cache_lock:
        _record_cache.pop(sheet_name, None)

def append_row(sheet_name: str, row: list):
    sheet = get_sheet(sheet_name)
    sheet.append_row(row)
    invalidate_cache(sheet_name)


def append_rows_atomically(rows_by_sheet: list[tuple[str, list]]) -> None:
    spreadsheet = get_spreadsheet()
    requests = []
    rows_by_name = {}
    for sheet_name, values in rows_by_sheet:
        rows_by_name.setdefault(sheet_name, []).append(values)

    for sheet_name, rows in rows_by_name.items():
        worksheet = get_sheet(sheet_name, create=False)
        if not worksheet:
            raise ValueError(f"Worksheet not found: {sheet_name}")
        request_rows = []
        for values in rows:
            cells = []
            for value in values:
                if isinstance(value, bool):
                    cell_value = {"boolValue": value}
                elif isinstance(value, (int, float)):
                    cell_value = {"numberValue": value}
                else:
                    cell_value = {"stringValue": str(value or "")}
                cells.append({"userEnteredValue": cell_value})
            request_rows.append({"values": cells})
        requests.append({
            "appendCells": {
                "sheetId": worksheet.id,
                "rows": request_rows,
                "fields": "userEnteredValue",
            }
        })

    spreadsheet.batch_update({"requests": requests})
    for sheet_name in rows_by_name:
        invalidate_cache(sheet_name)


def archive_daily_detail(archive_month: str, rows: list[list], new_period_label: str) -> int:
    source = get_sheet("(2)Rincian Transaksi Harian", create=False)
    if not source:
        raise ValueError("Worksheet rincian transaksi harian tidak ditemukan")
    archive = get_sheet("_ArsipRincianHarian")
    archive_headers = [
        "Bulan Arsip", "Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi",
        "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)",
    ]
    if not archive.get("1:1") or not any(str(value).strip() for value in archive.get("1:1")[0]):
        archive.update(range_name="A1:H1", values=[archive_headers])

    archived_rows = [
        [archive_month, *(row[:7] + [""] * max(0, 7 - len(row)))]
        for row in rows
        if any(str(value).strip() for value in row)
    ]
    if archived_rows:
        archive.append_rows(archived_rows)

    while len(source.get_all_values()) > 5:
        source.delete_rows(6)

    source.update(range_name="A3", values=[[new_period_label]])
    invalidate_cache("(2)Rincian Transaksi Harian")
    invalidate_cache("_ArsipRincianHarian")
    return len(archived_rows)


def update_cell(sheet_name: str, row: int, column: int, value):
    """Update a worksheet cell using Google Sheets' 1-based coordinates."""
    sheet = get_sheet(sheet_name, create=False)
    if not sheet:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    sheet.update_cell(row, column, value)
    invalidate_cache(sheet_name)

def delete_row(sheet_name: str, row: int):
    sheet = get_sheet(sheet_name, create=False)
    if not sheet:
        return
    sheet.delete_rows(row)
    invalidate_cache(sheet_name)

def get_drive_access_token() -> str:
    from app.services.drive_oauth import get_drive_credentials
    return get_drive_credentials().token

def get_sales_data():
    sheet = get_sheet("Rincian Penjualan", create=False)
    if not sheet: return []
    return sheet.get_all_records()

def _records_from_values(values: list[list]) -> list[dict]:
    if not values:
        return []

    title = str(values[0][0]).strip().lower() if values[0] else ""
    header_row = 1 if title.startswith("kakorlap unit usaha") and len(values) > 1 else 0
    if header_row >= len(values):
        return []

    headers = []
    used_headers = set()
    for column, value in enumerate(values[header_row], start=1):
        base = str(value).strip() or f"Column_{column}"
        header = base
        suffix = 2
        while header in used_headers:
            header = f"{base}_{suffix}"
            suffix += 1
        headers.append(header)
        used_headers.add(header)

    records = []
    for row in values[header_row + 1:]:
        if not any(str(value).strip() for value in row):
            continue
        records.append({
            header: numericise(row[index], default_blank="") if index < len(row) else ""
            for index, header in enumerate(headers)
        })
    return records


def _a1_column_range(sheet_name: str) -> str:
    sheet_reference = sheet_name if _SIMPLE_SHEET_NAME.fullmatch(sheet_name) else f"'{sheet_name.replace(chr(39), chr(39) * 2)}'"
    return f"{sheet_reference}!A:Z"


def get_all_records_many(sheet_names: list[str]) -> dict[str, list[dict]]:
    with _cache_lock:
        now = monotonic()
        records_by_sheet = {}
        uncached_names = []
        for name in dict.fromkeys(sheet_names):
            cached = _record_cache.get(name)
            if cached and cached[0] > now:
                records_by_sheet[name] = cached[1]
            else:
                uncached_names.append(name)

        if uncached_names:
            if len(uncached_names) == 1:
                name = uncached_names[0]
                worksheet = get_sheet(name, create=False)
                values = worksheet.get_all_values() if worksheet else []
                value_sets = [(name, values)]
            else:
                ranges = [_a1_column_range(name) for name in uncached_names]
                response = get_spreadsheet().values_batch_get(ranges, params={"majorDimension": "ROWS"})
                value_sets = [
                    (name, value_range.get("values", []))
                    for name, value_range in zip(uncached_names, response.get("valueRanges", []))
                ]
            for name, values in value_sets:
                records = _records_from_values(values)
                _record_cache[name] = (monotonic() + _READ_CACHE_SECONDS, records)
                records_by_sheet[name] = records

        return {
            name: [record.copy() for record in records_by_sheet.get(name, [])]
            for name in sheet_names
        }


def get_all_records(sheet_name: str) -> list:
    return get_all_records_many([sheet_name])[sheet_name]