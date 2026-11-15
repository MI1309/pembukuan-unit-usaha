import hashlib
import json
import logging
import re
from datetime import date, datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from threading import RLock
from gspread.utils import numericise
from time import monotonic

from app.config import config
from app.core.utils import parse_amount
from app.core.retry import retry_google_api, safe_append_with_idempotency
from app.core.transaction_id import generate_transaction_id

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
_TRANSACTION_ID = re.compile(r"^\d{8}-[A-Z]{3}-[A-Fa-f0-9]{8}$")
logger = logging.getLogger(__name__)


class ArchiveVerificationError(RuntimeError):
    def __init__(self, message, missing_ids=None, extra_ids=None):
        super().__init__(message)
        self.missing_ids = sorted(missing_ids or [])
        self.extra_ids = sorted(extra_ids or [])


def _retry_google_call(operation, *args, **kwargs):
    return retry_google_api()(operation)(*args, **kwargs)


def _find_existing_transaction_ids(sheet_names, transaction_ids):
    transaction_ids = set(transaction_ids)
    found = set()
    for sheet_name in sheet_names:
        worksheet = get_sheet(sheet_name, create=False)
        if worksheet:
            
            found.update(transaction_ids.intersection(
                str(value).strip()
                for row in values
                for value in row
            ))
    return found


def _transaction_ids_in_rows(rows):
    return {
        str(row[-1]).strip()
        for row in rows
        if row and isinstance(row[-1], str) and _TRANSACTION_ID.fullmatch(row[-1].strip())
    }


def _safe_append(operation, rows, sheet_names, operation_name):
    transaction_ids = _transaction_ids_in_rows(rows)
    return safe_append_with_idempotency(
        operation,
        transaction_ids,
        lambda ids: _find_existing_transaction_ids(sheet_names, ids),
        operation_name=operation_name,
    )


def _column_name(column: int) -> str:
    name = ""
    while column > 0:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _normalize_header_label(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _detect_header_row_index(sheet_name: str, values: list[list]) -> int:
    clean_name = str(sheet_name or "").strip()
    is_title_banner = bool(
        values and len(values) > 0 and len(values[0]) > 0
        and str(values[0][0]).strip().lower().startswith(("kakorlap", "industri pertanian"))
    )

    if is_title_banner:
        if clean_name == "(2)Rincian Transaksi Harian":
            return 4
        if clean_name in config.UNIT_USAHA or clean_name.startswith(("(3)", "(4)", "(5)", "(6)", "(7)")):
            return 1

    if clean_name == "(2)Rincian Transaksi Harian" and len(values) >= 5:
        row5 = [str(c).strip().casefold() for c in values[4]]
        if "transaction id" in row5 or "tanggal" in row5:
            return 4

    for idx, row in enumerate(values):
        non_empty = [str(cell).strip() for cell in row if str(cell).strip()]
        if len(non_empty) < 3:
            continue
        row_lower = [c.casefold() for c in non_empty]
        if any(keyword in row_lower for keyword in ("tanggal", "tgl", "nama barang", "nama produk", "produk", "barang")):
            return idx

    for idx, row in enumerate(values):
        non_empty = [str(cell).strip() for cell in row if str(cell).strip()]
        if len(non_empty) >= 3 and any(c.casefold() == "transaction id" for c in non_empty):
            return idx

    for idx, row in enumerate(values):
        if any(str(cell).strip() for cell in row):
            return idx

    return 0


def _cached_get_all_values(worksheet):
    """Return all values from *worksheet* using a short‑lived in‑memory cache.
    The cache expires after :data:`_READ_CACHE_SECONDS` seconds to keep data
    reasonably fresh while dramatically reducing repeated ``get_all_values``
    calls that trigger Google Sheets read quota consumption.
    """
    now = monotonic()
    cache_key = getattr(worksheet, "title", "unknown")
    with _cache_lock:
        entry = _record_cache.get(cache_key)
        if entry and now - entry["timestamp"] < _READ_CACHE_SECONDS:
            return entry["values"]
        # Cache miss – fetch from API and store
        values = _retry_google_call(worksheet.get_all_values)
        _record_cache[cache_key] = {"timestamp": now, "values": values}
        return values

    
    if not values:
        return False

    header_row_index = _detect_header_row_index(getattr(worksheet, "title", ""), values)
    header_row_number = header_row_index + 1

    # Bersihkan cell B1 jika baris 1 adalah banner judul yang sebelumnya terisi "Transaction ID"
    if header_row_index > 0 and len(values) > 0 and len(values[0]) > 1:
        if str(values[0][1]).strip().casefold() == "transaction id":
            _retry_google_call(worksheet.update_cell, 1, 2, "")
            values[0][1] = ""

    while len(values) <= header_row_index:
        values.append([])

    header = [str(cell).strip() for cell in values[header_row_index]]
    normalized = [_normalize_header_label(cell) for cell in header]

    if "transaction id" in normalized:
        return False

    repaired = []
    for index, value in enumerate(header):
        label = normalized[index]
        if label in {"nomor", "no", "no.", "id transaksi"}:
            continue
        if label == "transaction id":
            continue
        repaired.append(value)

    insert_index = len(repaired)
    for idx, value in enumerate(repaired):
        if _normalize_header_label(value) == "sisa saldo":
            insert_index = idx + 1
            break
    if insert_index == len(repaired):
        for idx, value in enumerate(repaired):
            if _normalize_header_label(value) == "pengeluaran (rp)":
                insert_index = idx + 1
                break

    repaired.insert(insert_index, "Transaction ID")
    width = max(len(repaired), len(header))
    padded = repaired + [""] * max(0, width - len(repaired))
    worksheet.update(range_name=f"A{header_row_number}:{_column_name(width)}{header_row_number}", values=[padded])

    return True


def _parse_sheet_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y.%m.%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _sort_rows_by_date(rows, date_column_index):
    def _date_index_for(row):
        candidates = [date_column_index, max(0, date_column_index - 1), 0, 1, 2]
        for idx in candidates:
            if idx < len(row) and _parse_sheet_date(row[idx]) is not None:
                return idx
        for idx, value in enumerate(row):
            if _parse_sheet_date(value) is not None:
                return idx
        return max(0, date_column_index - 1)

    def _sort_key(row):
        idx = _date_index_for(row)
        parsed = _parse_sheet_date(row[idx] if idx < len(row) else "")
        if parsed is None:
            return (9999, 12, 31)
        return (parsed.year, parsed.month, parsed.day)

    return sorted(rows, key=_sort_key)


def _safe_delete_rows(worksheet, row_number, expected_values=None):
    if expected_values is None:
        expected_values = _retry_google_call(worksheet.row_values, row_number)

    def delete_if_unchanged():
        current_values = _retry_google_call(worksheet.row_values, row_number)
        if current_values != expected_values:
            return
        return worksheet.delete_rows(row_number)

    return _retry_google_call(delete_if_unchanged)


def get_client():
    global creds, client
    if client is None:
        with _spreadsheet_lock:
            if client is None:
                creds = ServiceAccountCredentials.from_json_keyfile_name(config.GOOGLE_CREDENTIALS_FILE, scope)
                client = gspread.authorize(creds)
    return client


@retry_google_api()
def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        with _spreadsheet_lock:
            if _spreadsheet is None:
                _spreadsheet = get_client().open_by_key(config.SPREADSHEET_ID)
    return _spreadsheet

def get_sheet(name: str, create=True):
    """Retrieve a worksheet, creating it if needed.
    The result is cached in ``_worksheet_cache`` to avoid repeated API calls.
    """
    with _cache_lock:
        if name in _worksheet_cache:
            worksheet = _worksheet_cache[name]
            if worksheet is not None or not create:
                return worksheet

        spreadsheet = get_spreadsheet()
        try:
            worksheet = _retry_google_call(spreadsheet.worksheet, name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=name, rows=1000, cols=20) if create else None
        if worksheet is not None:
            _worksheet_cache[name] = worksheet
        return worksheet
    with _cache_lock:
        if name in _worksheet_cache:
            worksheet = _worksheet_cache[name]
            if worksheet is not None or not create:
                return worksheet

        spreadsheet = get_spreadsheet()
        try:
            worksheet = _retry_google_call(spreadsheet.worksheet, name)
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
    _safe_append(lambda: sheet.append_row(row), [row], [sheet_name], f"append_row:{sheet_name}")
    invalidate_cache(sheet_name)


def _appended_cell_format(sheet_name: str, column: int) -> dict:
    cell_format = {
        "backgroundColor": {"red": 0.97, "green": 0.98, "blue": 1.0},
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP",
        "horizontalAlignment": "LEFT",
        "borders": {
            "bottom": {
                "style": "SOLID",
                "color": {"red": 0.86, "green": 0.89, "blue": 0.92},
            }
        },
    }
    if sheet_name == "(2)Rincian Transaksi Harian":
        if column == 0:
            cell_format["numberFormat"] = {"type": "DATE", "pattern": "dd mmm yyyy"}
            cell_format["horizontalAlignment"] = "CENTER"
        elif column in {4, 5, 6}:
            cell_format["numberFormat"] = {"type": "CURRENCY", "pattern": '"Rp" #,##0'}
            cell_format["horizontalAlignment"] = "RIGHT"
    elif sheet_name.startswith(("(3)", "(4)", "(5)", "(6)", "(7)")):
        if column == 1:
            cell_format["numberFormat"] = {"type": "DATE", "pattern": "dd mmm yyyy"}
            cell_format["horizontalAlignment"] = "CENTER"
        elif column in {5, 6, 7, 8}:
            cell_format["numberFormat"] = {"type": "CURRENCY", "pattern": '"Rp" #,##0'}
            cell_format["horizontalAlignment"] = "RIGHT"
    return cell_format


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
        _ensure_transaction_id_header(worksheet)
        request_rows = []
        date_columns = set()
        for values in rows:
            cells = []
            for column, value in enumerate(values):
                if isinstance(value, bool):
                    cell_value = {"boolValue": value}
                elif isinstance(value, date):
                    cell_value = {"numberValue": (value - date(1899, 12, 30)).days}
                    date_columns.add(column)
                elif isinstance(value, (int, float)):
                    cell_value = {"numberValue": value}
                else:
                    cell_value = {"stringValue": str(value or "")}
                cells.append({
                    "userEnteredValue": cell_value,
                    "userEnteredFormat": _appended_cell_format(sheet_name, column),
                })
            request_rows.append({"values": cells})
        requests.append({
            "appendCells": {
                "sheetId": worksheet.id,
                "rows": request_rows,
                "fields": "userEnteredValue,userEnteredFormat",
            }
        })
        for column in date_columns:
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "cell": {"userEnteredFormat": {"numberFormat": {
                        "type": "DATE", "pattern": "dd mmm yyyy",
                    }}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

    _safe_append(
        lambda: spreadsheet.batch_update({"requests": requests}),
        [values for rows in rows_by_name.values() for values in rows],
        list(rows_by_name),
        "append_rows_atomically",
    )
    for sheet_name in rows_by_name:
        worksheet = get_sheet(sheet_name, create=False)
        if not worksheet:
            continue
        values = worksheet.get_all_values()
        if sheet_name == "(2)Rincian Transaksi Harian":
            if len(values) > 5:
                data_rows = _sort_rows_by_date(values[5:], 1)
                if data_rows:
                    worksheet.update(range_name=f"A6:{_column_name(max(len(row) for row in data_rows))}{5 + len(data_rows)}", values=data_rows)
        elif sheet_name in config.UNIT_USAHA:
            if len(values) > 1:
                data_rows = _sort_rows_by_date(values[1:], 1)
                if data_rows:
                    worksheet.update(range_name=f"A2:{_column_name(max(len(row) for row in data_rows))}{1 + len(data_rows)}", values=data_rows)
        invalidate_cache(sheet_name)


def _format_daily_archive(worksheet) -> None:
    sheet_id = worksheet.id
    metadata = _retry_google_call(
        worksheet.spreadsheet.fetch_sheet_metadata,
        params={"includeGridData": False},
    )
    sheet_metadata = next(
        sheet for sheet in metadata.get("sheets", [])
        if sheet.get("properties", {}).get("sheetId") == sheet_id
    )
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                    "textFormat": {
                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        "bold": True,
                    },
                    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 11},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }},
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                    "borders": {"bottom": {
                        "style": "SOLID",
                        "color": {"red": 0.86, "green": 0.89, "blue": 0.92},
                    }},
                }},
                "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,userEnteredFormat.borders",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.89, "green": 0.95, "blue": 0.92},
                    "textFormat": {"bold": True, "foregroundColor": {"red": 0.16, "green": 0.36, "blue": 0.29}},
                    "horizontalAlignment": "CENTER",
                }},
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat,userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd mmm yyyy"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 6,
                    "endColumnIndex": 8,
                },
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "CURRENCY", "pattern": '"Rp" #,##0'},
                    "horizontalAlignment": "RIGHT",
                }},
                "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1, "hideGridlines": True}},
                "fields": "gridProperties.frozenRowCount,gridProperties.hideGridlines",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 42},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": 1000},
                "properties": {"pixelSize": 34},
                "fields": "pixelSize",
            }
        },
        {
            "setBasicFilter": {
                "filter": {"range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "startColumnIndex": 0,
                    "endColumnIndex": 8,
                }}
            }
        },
    ]
    for banded_range in sheet_metadata.get("bandedRanges", []):
        banded_range_id = banded_range.get("bandedRangeId")
        if banded_range_id is not None:
            requests.append({"deleteBanding": {"bandedRangeId": banded_range_id}})
    requests.append({
        "addBanding": {"bandedRange": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "startColumnIndex": 0,
                "endColumnIndex": 8,
            },
            "rowProperties": {
                "headerColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                "firstBandColor": {"red": 1, "green": 1, "blue": 1},
                "secondBandColor": {"red": 0.93, "green": 0.96, "blue": 0.98},
            },
        }}
    })
    widths = [115, 125, 230, 320, 120, 130, 155, 155]
    requests.extend({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": column,
                "endIndex": column + 1,
            },
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }
    } for column, width in enumerate(widths))
    _retry_google_call(worksheet.spreadsheet.batch_update, {"requests": requests})


def archive_daily_detail(archive_month: str, rows: list[list], new_period_label: str) -> int:
    source = get_sheet("(2)Rincian Transaksi Harian", create=False)
    if not source:
        raise ValueError("Worksheet rincian transaksi harian tidak ditemukan")
    archive = get_sheet("_ArsipRincianHarian")
    archive_headers = [
        "Bulan Arsip", "Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi",
        "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)",
    ]
    archive_header = _retry_google_call(archive.get, "1:1")
    if not archive_header or not any(str(value).strip() for value in archive_header[0]):
        _retry_google_call(archive.update, range_name="A1:H1", values=[archive_headers])

    archived_rows = [
        [archive_month, *(row[:7] + [""] * max(0, 7 - len(row)))]
        for row in rows
        if any(str(value).strip() for value in row)
    ]
    if archived_rows:
        _safe_append(
            lambda: archive.append_rows(archived_rows), archived_rows,
            ["_ArsipRincianHarian"], "archive_daily_detail_append",
        )
    _format_daily_archive(archive)

    while len(_retry_google_call(source.get_all_values)) > 5:
        _safe_delete_rows(source, 6)

    _retry_google_call(source.update, range_name="A3", values=[[new_period_label]])
    invalidate_cache("(2)Rincian Transaksi Harian")
    invalidate_cache("_ArsipRincianHarian")
    return len(archived_rows)


def update_cell(sheet_name: str, row: int, column: int, value):
    """Update a worksheet cell using Google Sheets' 1-based coordinates."""
    sheet = get_sheet(sheet_name, create=False)
    if not sheet:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    _retry_google_call(sheet.update_cell, row, column, value)
    invalidate_cache(sheet_name)

def delete_row(sheet_name: str, row: int):
    sheet = get_sheet(sheet_name, create=False)
    if not sheet:
        return
    _safe_delete_rows(sheet, row)
    invalidate_cache(sheet_name)

def get_drive_access_token() -> str:
    from app.services.drive_oauth import get_drive_credentials
    return get_drive_credentials().token

def get_sales_data():
    sheet = get_sheet("Rincian Penjualan", create=False)
    if not sheet: return []
    return _retry_google_call(sheet.get_all_records)

def _records_from_values(values: list[list]) -> list[dict]:
    if not values:
        return []

    title = str(values[0][0]).strip().lower() if values[0] else ""
    if title.startswith("kakorlap unit usaha"):
        header_row = 1
    elif title.startswith("industri pertanian terpadu"):
        header_row = next(
            (index for index, row in enumerate(values[1:], start=1)
             if row and str(row[0]).strip().lower() == "tanggal"
             and len(row) > 1 and "produk" in str(row[1]).strip().lower()),
            len(values),
        )
    else:
        header_row = 0
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
                 if worksheet else []
                value_sets = [(name, values)]
            else:
                ranges = [_a1_column_range(name) for name in uncached_names]
                response = _retry_google_call(
                    get_spreadsheet().values_batch_get,
                    ranges,
                    params={"majorDimension": "ROWS"},
                )
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


def find_transaction_ids(sheet_names: list[str], tx_ids: list[str]) -> dict[str, list[str]]:
    requested_ids = {str(tx_id).strip() for tx_id in tx_ids if str(tx_id).strip()}
    found = {tx_id: [] for tx_id in requested_ids}
    for sheet_name in dict.fromkeys(sheet_names):
        worksheet = get_sheet(sheet_name, create=False)
        if not worksheet:
            continue
        
        header_index = next(
            (index for index, row in enumerate(values)
             if any(str(value).strip().casefold() == "transaction id" for value in row)),
            None,
        )
        if header_index is None:
            continue
        tx_column = next(
            index for index, value in enumerate(values[header_index])
            if str(value).strip().casefold() == "transaction id"
        )
        for row in values[header_index + 1:]:
            tx_id = str(row[tx_column]).strip() if tx_column < len(row) else ""
            if tx_id in requested_ids and sheet_name not in found[tx_id]:
                found[tx_id].append(sheet_name)
    return found


def _transaction_id_column(worksheet, header_row_number):
    
    header_index = header_row_number - 1
    while len(values) <= header_index:
        values.append([])
    headers = values[header_index]
    for column, header in enumerate(headers):
        if str(header).strip().casefold() == "transaction id":
            return column, values
    column = len(headers)
    _retry_google_call(worksheet.update_cell, header_row_number, column + 1, "Transaction ID")
    return column, values


def _read_transaction_id_column(values, header_row_number):
    header_index = header_row_number - 1
    if header_index >= len(values):
        return None
    for column, header in enumerate(values[header_index]):
        if str(header).strip().casefold() == "transaction id":
            return column
    return None


def _row_transaction_id(row, column):
    return str(row[column]).strip() if column < len(row) else ""


def _legacy_row_transaction_id(unit, row_number, row):
    values = list(row[:9]) + [""] * max(0, 9 - len(row))
    nominal = parse_amount(values[6]) or parse_amount(values[7])
    return generate_transaction_id(
        f"legacy:{unit}:{row_number}", values[1], values[2], values[4],
        nominal, values[3], unit,
    )


def _transaction_ids_hash(tx_ids):
    return hashlib.sha256("\n".join(sorted(tx_ids)).encode("utf-8")).hexdigest()


def _read_archive_manifest(manifest_key):
    metadata_sheet = get_sheet("_Meta", create=False)
    if not metadata_sheet:
        raise ValueError("Worksheet _Meta tidak ditemukan")
    values = _retry_google_call(metadata_sheet.get_all_values)
    for row in values:
        if len(row) >= 2 and row[0] == manifest_key:
            try:
                return json.loads(row[1])
            except (TypeError, ValueError) as error:
                raise ValueError(f"Manifest arsip tidak valid: {manifest_key}") from error
    raise ValueError(f"Manifest arsip tidak ditemukan: {manifest_key}")


def _write_archive_manifest(manifest_key, manifest):
    metadata_sheet = get_sheet("_Meta")
    values = _retry_google_call(metadata_sheet.get_all_values)
    if not values:
        _retry_google_call(metadata_sheet.update, range_name="A1:B1", values=[["Key", "Value"]])
        values = [["Key", "Value"]]
    serialized = json.dumps(manifest, sort_keys=True)
    for row_number, row in enumerate(values[1:], start=2):
        if row and row[0] == manifest_key:
            _retry_google_call(metadata_sheet.update_cell, row_number, 2, serialized)
            invalidate_cache("_Meta")
            return
    safe_append_with_idempotency(
        lambda: metadata_sheet.append_row([manifest_key, serialized]),
        [manifest_key],
        lambda keys: {
            key for row in _retry_google_call(metadata_sheet.get_all_values)
            if row for key in keys if row[0] == key
        },
        operation_name="archive_manifest_append",
    )
    invalidate_cache("_Meta")


def _log_archive_verification_failure(archived_at, missing_ids, extra_ids, error=None):
    from app.core.retry import error_code
    details = json.dumps({
        "archived_at": archived_at,
        "missing_ids": sorted(missing_ids),
        "extra_ids": sorted(extra_ids),
        "error_type": type(error).__name__ if error else "",
        "error_code": error_code(error) if error else None,
    }, sort_keys=True)
    logger.error("ARCHIVE_VERIFY_FAILED %s", details)
    try:
        from app.services.webhook_state import log_event
        log_event("system", "", "ARCHIVE_VERIFY_FAILED", error=details)
    except Exception as logging_error:
        logger.warning("Unable to persist archive verification failure log error_type=%s", type(logging_error).__name__)


def archive_unit_transactions(rows_by_unit: dict[str, list[tuple[int, list]]], archived_at: str) -> int:
    archive = get_sheet("_ArsipTransaksi")
    archive_id_column, archive_values_before = _transaction_id_column(archive, 1)
    archive_headers = archive_values_before[0] if archive_values_before else []
    archived_at_column = next(
        (index for index, value in enumerate(archive_headers)
         if str(value).strip().casefold() == "diarsipkan"),
        10,
    )
    before_ids = {
        _row_transaction_id(row, archive_id_column)
        for row in archive_values_before[1:]
        if _row_transaction_id(row, archive_id_column)
    }
    archived_rows = []
    source_ids_by_unit = {}
    source_sheets = {}
    delete_rows_by_unit = {}
    for unit, rows in rows_by_unit.items():
        source = get_sheet(unit, create=False)
        if not source:
            error = ArchiveVerificationError(f"Worksheet sumber tidak ditemukan: {unit}")
            _log_archive_verification_failure(archived_at, [], [], error)
            raise error
        source_id_column, source_values = _transaction_id_column(source, 2)
        source_sheets[unit] = source
        unit_ids = []
        delete_rows = []
        for row_number, provided_row in rows:
            current_row = source_values[row_number - 1] if row_number - 1 < len(source_values) else []
            if [str(value) for value in current_row] != [str(value) for value in provided_row]:
                error = ArchiveVerificationError(f"Baris sumber berubah: {unit} row {row_number}")
                _log_archive_verification_failure(archived_at, [], [], error)
                raise error
            values = list(current_row[:9]) + [""] * max(0, 9 - len(current_row))
            transaction_id = _row_transaction_id(current_row, source_id_column)
            if not transaction_id:
                transaction_id = _legacy_row_transaction_id(unit, row_number, values)
                _retry_google_call(source.update_cell, row_number, source_id_column + 1, transaction_id)
                while len(current_row) <= source_id_column:
                    current_row.append("")
                current_row[source_id_column] = transaction_id
                source_values[row_number - 1] = current_row
            unit_ids.append(transaction_id)
            delete_rows.append((row_number, list(current_row)))
            archived_rows.append([unit, *values, archived_at, transaction_id])
        source_ids_by_unit[unit] = unit_ids
        delete_rows_by_unit[unit] = delete_rows
    if archived_rows:
        requested_ids = [transaction_id for ids in source_ids_by_unit.values() for transaction_id in ids]
        existing_ids = find_transaction_ids(["_ArsipTransaksi"], requested_ids)
        rows_to_append = [row for row in archived_rows if not existing_ids.get(row[-1])]
        append_error = None
        if rows_to_append:
            try:
                _safe_append(
                    lambda: archive.append_rows(rows_to_append, value_input_option="USER_ENTERED"),
                    rows_to_append,
                    ["_ArsipTransaksi"],
                    "archive_unit_transactions_append",
                )
            except Exception as error:
                append_error = error

        archive_values_after = _retry_google_call(archive.get_all_values)
        found_ids_by_unit = {unit: set() for unit in rows_by_unit}
        for row in archive_values_after[1:]:
            transaction_id = _row_transaction_id(row, archive_id_column)
            archived_value = str(row[archived_at_column]).strip() if archived_at_column < len(row) else ""
            unit = str(row[0]) if row else ""
            if archived_value == archived_at and unit in found_ids_by_unit:
                found_ids_by_unit[unit].add(transaction_id)

        expected_by_unit = {unit: set(ids) for unit, ids in source_ids_by_unit.items()}
        missing_ids = set().union(*(
            expected_by_unit[unit] - found_ids_by_unit[unit] for unit in expected_by_unit
        ))
        added_ids = {
            _row_transaction_id(row, archive_id_column)
            for row in archive_values_after[1:]
            if _row_transaction_id(row, archive_id_column)
            and _row_transaction_id(row, archive_id_column) not in before_ids
        }
        all_expected_ids = set().union(*expected_by_unit.values()) if expected_by_unit else set()
        extra_ids = added_ids - all_expected_ids
        if missing_ids or extra_ids or append_error:
            _log_archive_verification_failure(archived_at, missing_ids, extra_ids, append_error)
            raise ArchiveVerificationError(
                f"Verifikasi arsip gagal untuk {archived_at}", missing_ids, extra_ids
            )

        verified_at = datetime.now().astimezone().isoformat()
        manifest = {
            unit: {
                "count": len(ids),
                "tx_ids_hash": _transaction_ids_hash(set(ids)),
                "verified_at": verified_at,
            }
            for unit, ids in source_ids_by_unit.items()
        }
        _write_archive_manifest(f"archive_manifest:{archived_at}", manifest)

        for unit, rows in rows_by_unit.items():
            worksheet = source_sheets[unit]
            for row_number, row_values in reversed(delete_rows_by_unit[unit]):
                _safe_delete_rows(worksheet, row_number, row_values)
            invalidate_cache(unit)
    invalidate_cache("_ArsipTransaksi")
    return len(archived_rows)


def verify_archive_manifest(manifest_key: str) -> dict:
    manifest = _read_archive_manifest(manifest_key)
    archived_at = manifest_key.removeprefix("archive_manifest:")
    archive = get_sheet("_ArsipTransaksi", create=False)
    if not archive:
        raise ValueError("Worksheet _ArsipTransaksi tidak ditemukan")
    values = _retry_google_call(archive.get_all_values)
    tx_column = _read_transaction_id_column(values, 1)
    if tx_column is None:
        raise ArchiveVerificationError("Kolom Transaction ID tidak ditemukan di arsip")
    headers = values[0] if values else []
    archived_column = next(
        (index for index, value in enumerate(headers)
         if str(value).strip().casefold() == "diarsipkan"),
        10,
    )
    rows_by_unit = {unit: [] for unit in manifest}
    for row in values[1:]:
        unit = str(row[0]) if row else ""
        archived_value = str(row[archived_column]).strip() if archived_column < len(row) else ""
        if unit in rows_by_unit and archived_value == archived_at:
            transaction_id = _row_transaction_id(row, tx_column)
            if transaction_id:
                rows_by_unit[unit].append(transaction_id)
    result = {}
    for unit, expected in manifest.items():
        actual_ids = set(rows_by_unit[unit])
        result[unit] = {
            "valid": len(rows_by_unit[unit]) == expected.get("count")
            and _transaction_ids_hash(actual_ids) == expected.get("tx_ids_hash"),
            "count": len(rows_by_unit[unit]),
            "tx_ids_hash": _transaction_ids_hash(actual_ids),
        }
    return result


def rollback_archive(manifest_key: str, apply: bool = True) -> dict:
    verification = verify_archive_manifest(manifest_key)
    if any(not result["valid"] for result in verification.values()):
        raise ArchiveVerificationError(f"Manifest arsip tidak lolos verifikasi: {manifest_key}")
    manifest = _read_archive_manifest(manifest_key)
    archived_at = manifest_key.removeprefix("archive_manifest:")
    archive = get_sheet("_ArsipTransaksi", create=False)
    values = _retry_google_call(archive.get_all_values)
    tx_column = _read_transaction_id_column(values, 1)
    if tx_column is None:
        raise ArchiveVerificationError("Kolom Transaction ID tidak ditemukan di arsip")
    headers = values[0] if values else []
    archived_column = next(
        (index for index, value in enumerate(headers)
         if str(value).strip().casefold() == "diarsipkan"),
        10,
    )
    archived_by_unit = {unit: [] for unit in manifest}
    for row in values[1:]:
        unit = str(row[0]) if row else ""
        archived_value = str(row[archived_column]).strip() if archived_column < len(row) else ""
        transaction_id = _row_transaction_id(row, tx_column)
        if unit in archived_by_unit and archived_value == archived_at and transaction_id:
            archived_by_unit[unit].append((transaction_id, list(row[1:10]) + [transaction_id]))

    missing_by_unit = {}
    for unit, archived_rows in archived_by_unit.items():
        worksheet = get_sheet(unit, create=False)
        if not worksheet:
            raise ValueError(f"Worksheet ledger tidak ditemukan: {unit}")
        if apply:
            _transaction_id_column(worksheet, 2)
        found = find_transaction_ids([unit], [tx_id for tx_id, _ in archived_rows])
        missing_by_unit[unit] = [row for tx_id, row in archived_rows if not found.get(tx_id)]
    result = {unit: len(rows) for unit, rows in missing_by_unit.items()}
    if apply:
        rows_to_restore = [
            (unit, row)
            for unit, rows in missing_by_unit.items()
            for row in rows
        ]
        if rows_to_restore:
            append_rows_atomically(rows_to_restore)
    return result