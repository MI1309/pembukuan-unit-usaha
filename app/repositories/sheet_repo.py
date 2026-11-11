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

    headers = []
    used_headers = set()
    for column, value in enumerate(values[0], start=1):
        base = str(value).strip() or f"Column_{column}"
        header = base
        suffix = 2
        while header in used_headers:
            header = f"{base}_{suffix}"
            suffix += 1
        headers.append(header)
        used_headers.add(header)

    records = []
    for row in values[1:]:
        if not any(str(value).strip() for value in row):
            continue
        records.append({
            header: numericise(row[index], default_blank="") if index < len(row) else ""
            for index, header in enumerate(headers)
        })
    return records


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
            ranges = [f"'{name.replace(chr(39), chr(39) * 2)}'!A:Z" for name in uncached_names]
            response = get_spreadsheet().values_batch_get(ranges, params={"majorDimension": "ROWS"})
            value_ranges = response.get("valueRanges", [])
            for name, value_range in zip(uncached_names, value_ranges):
                records = _records_from_values(value_range.get("values", []))
                _record_cache[name] = (monotonic() + _READ_CACHE_SECONDS, records)
                records_by_sheet[name] = records

        return {
            name: [record.copy() for record in records_by_sheet.get(name, [])]
            for name in sheet_names
        }


def get_all_records(sheet_name: str) -> list:
    return get_all_records_many([sheet_name])[sheet_name]