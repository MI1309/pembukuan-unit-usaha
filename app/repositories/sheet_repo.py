import hashlib
import json
import logging
import re
from datetime import date, datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from threading import RLock
from gspread.utils import numericise
from time import monotonic, sleep

from app.config import config
from app.core.utils import parse_amount, parse_date
from app.core.retry import retry_google_api, safe_append_with_idempotency
from app.core.transaction_id import generate_transaction_id
from app.core.metrics import track_sheets

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = None
client = None
_spreadsheet = None
_spreadsheet_lock = RLock()
_worksheet_cache = {}
_record_cache = {}
_cache_lock = RLock()

# Optimasi TTL: Master data 30s, Ledger/Logs 3s
def _get_ttl(sheet_name: str) -> int:
    name = str(sheet_name or "").strip()
    if name in {config.SHEET_KARYAWAN, "_Meta"} or "Produk" in name:
        return 30
    return 3
_SIMPLE_SHEET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRANSACTION_ID = re.compile(r"^\d{8}-[A-Z]{3}-[A-Fa-f0-9]{8}$")
logger = logging.getLogger(__name__)


class ArchiveVerificationError(RuntimeError):
    def __init__(self, message, missing_ids=None, extra_ids=None):
        super().__init__(message)
        self.missing_ids = sorted(missing_ids or [])
        self.extra_ids = sorted(extra_ids or [])


class SheetRangeError(Exception):
    """Raised when a Google Sheets API call fails because a sheet or range is
    invalid/missing.  Carries a human-readable *hint* suitable for operator
    notifications."""
    def __init__(self, sheet: str, range_: str, hint: str):
        super().__init__(hint)
        self.sheet = sheet
        self.range_ = range_
        self.hint = hint


# Headers for _ArsipTransaksi — kept in sync with setup_reference_structure.SPECS
ARSIP_HEADERS: list[str] = [
    "Unit Usaha", "Nomor", "Tanggal", "Nama Barang/Jasa/Produk",
    "Keterangan", "Kuantitas", "Satuan", "Pemasukan (Rp)",
    "Pengeluaran (Rp)", "Saldo", "Diarsipkan", "Transaction ID",
]


def _retry_google_call(operation, *args, **kwargs):
    # Track metrics if it's a gspread/google API call
    op_name = getattr(operation, "__name__", str(operation))
    track_sheets(op_name)
    return retry_google_api()(operation)(*args, **kwargs)


def _quote_sheet_name(name: str) -> str:
    """Return a safely-quoted sheet name for use in A1 notation ranges."""
    if _SIMPLE_SHEET_NAME.fullmatch(name):
        return name
    return "'" + name.replace("'", "''") + "'"


# ============================================================
# BATCH OPT #1 — helper untuk membaca banyak range dalam 1 request.
# Google membatasi "request per menit", bukan "sel per menit".
# 1 batchGet berisi N range tetap dihitung 1 request.
# ============================================================
def _batch_get_ranges(ranges: list[str]) -> dict[str, list[list]]:
    """Baca banyak range A1 dalam SATU request ke Google Sheets.

    Args:
        ranges: List A1 notation, mis. ["(4)Ternak Ayam Fazza!A1:Z",
                "(5)Ternak Ayam Emran!A1:Z"].

    Returns:
        dict: {range_name: values} — values = list of rows.

    Menggunakan spreadsheets.values.batchGet agar N range = 1 request
    (bukan N request terpisah). Ini sangat penting untuk kuota 60 req/menit.
    """
    if not ranges:
        return {}
    response = _retry_google_call(
        get_spreadsheet().values_batch_get,
        ranges,
        params={"majorDimension": "ROWS"},
    )
    result = {}
    for range_name, value_range in zip(ranges, response.get("valueRanges", [])):
        result[range_name] = value_range.get("values", [])
    return result


def _batch_get_sheet_columns(sheet_names: list[str], column_letter: str) -> dict[str, list[str]]:
    """Baca satu kolom dari banyak sheet dalam 1 request.

    Mis. ambil kolom Transaction ID (kolom L) dari 5 unit sekaligus.
    N sheet = 1 request.
    """
    if not sheet_names:
        return {}
    ranges = [f"{_quote_sheet_name(name)}!{column_letter}:{column_letter}" for name in sheet_names]
    raw = _batch_get_ranges(ranges)
    result = {}
    for name, range_name in zip(sheet_names, ranges):
        rows = raw.get(range_name, [])
        result[name] = [row[0] if row else "" for row in rows]
    return result


# ============================================================
# FIX #3 + OPT #3 — delegasi ke find_transaction_ids yang efisien
# ============================================================
def _find_existing_transaction_ids(sheet_names, transaction_ids):
    found = find_transaction_ids(sheet_names, list(transaction_ids))
    return {tx_id for tx_id, sheets in found.items() if sheets}


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


# ============================================================
# FIX #2 — hapus kode mati & perbaiki logika TTL cache
# OPT #1 — cache dipakai konsisten di semua jalur baca non-real-time
# ============================================================
def _cached_get_all_values(worksheet):
    """Return all values from *worksheet* using a short-lived in-memory cache."""
    now = monotonic()
    cache_key = getattr(worksheet, "title", "unknown")
    with _cache_lock:
        entry = _record_cache.get(cache_key)
        ttl = _get_ttl(cache_key)
        if entry and now - entry["timestamp"] < ttl:
            return entry["values"]
        values = _retry_google_call(worksheet.get_all_values)
        _record_cache[cache_key] = {"timestamp": now, "values": values}
        return values


def invalidate_cache(sheet_name: str):
    with _cache_lock:
        if sheet_name in _record_cache:
            del _record_cache[sheet_name]


def invalidate_cache_all():
    with _cache_lock:
        _record_cache.clear()
        _worksheet_cache.clear()


# ============================================================
# FIX #1 — definisi _ensure_transaction_id_header
# OPT #4 — pakai cache header (jarang berubah)
# ============================================================
def _ensure_transaction_id_header(worksheet) -> bool:
    """Pastikan worksheet punya kolom 'Transaction ID' di header.
    Append-only, idempoten. Return True jika header baru ditambahkan."""
    values = _cached_get_all_values(worksheet)
    if not values:
        return False

    header_row_index = _detect_header_row_index(
        getattr(worksheet, "title", ""), values
    )
    header_row_number = header_row_index + 1

    if header_row_index > 0 and len(values) > 0 and len(values[0]) > 1:
        if str(values[0][1]).strip().casefold() == "transaction id":
            _retry_google_call(worksheet.update_cell, 1, 2, "")
            values[0][1] = ""
            invalidate_cache(getattr(worksheet, "title", ""))

    while len(values) <= header_row_index:
        values.append([])

    header = [str(cell).strip() for cell in values[header_row_index]]
    normalized = [_normalize_header_label(cell) for cell in header]

    if (
        header_row_index > 0
        and normalized
        and normalized[0] == "tanggal"
        and "sisa saldo" in normalized
        and "transaction id" in normalized
        and len(header) >= 9
        and (normalized.index("sisa saldo"), normalized.index("transaction id")) != (7, 8)
    ):
        header[7:9] = ["Sisa Saldo", "Transaction ID"]
        _retry_google_call(
            worksheet.update,
            range_name=f"A{header_row_number}:I{header_row_number}",
            values=[header[:9]],
        )
        invalidate_cache(getattr(worksheet, "title", ""))
        return True

    if "transaction id" in normalized:
        return False

    repaired = [
        value for index, value in enumerate(header)
        if normalized[index] not in {"nomor", "no", "no.", "id transaksi", "transaction id"}
    ]

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
    _retry_google_call(
        worksheet.update,
        range_name=f"A{header_row_number}:{_column_name(width)}{header_row_number}",
        values=[padded],
    )
    invalidate_cache(getattr(worksheet, "title", ""))
    return True


def _parse_sheet_date(value):
    return parse_date(value)


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


# ============================================================
# OPT #5 — sorting terpisah, dipanggil periodik (bukan jalur kritis)
# ============================================================
def sort_sheet_rows(sheet_name: str) -> bool:
    """Sort worksheet data rows by date. Panggil periodik (mis. daily_backup),
    BUKAN dari jalur kritis transaksi."""
    worksheet = get_sheet(sheet_name, create=False)
    if not worksheet:
        return False
    invalidate_cache(sheet_name)
    values = _retry_google_call(worksheet.get_all_values)
    if sheet_name == "(2)Rincian Transaksi Harian":
        if len(values) <= 5:
            return False
        data_rows = _sort_rows_by_date(values[5:], 1)
        if not data_rows:
            return False
        worksheet.update(
            range_name=f"A6:{_column_name(max(len(row) for row in data_rows))}{5 + len(data_rows)}",
            values=data_rows,
        )
    elif sheet_name in config.UNIT_USAHA:
        if len(values) <= 1:
            return False
        data_rows = _sort_rows_by_date(values[1:], 1)
        if not data_rows:
            return False
        worksheet.update(
            range_name=f"A2:{_column_name(max(len(row) for row in data_rows))}{1 + len(data_rows)}",
            values=data_rows,
        )
    else:
        return False
    invalidate_cache(sheet_name)
    return True


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


# ============================================================
# FIX #4 — hapus blok duplikat di get_sheet
# ============================================================
def get_sheet(name: str, create=True):
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


def _evict_sheet_cache(sheet_name: str) -> None:
    with _cache_lock:
        _record_cache.pop(sheet_name, None)
        _worksheet_cache.pop(sheet_name, None)


def _ensure_sheet_exists(name: str, headers: list[str]):
    """Return the named worksheet, auto-creating it with *headers* if missing."""
    try:
        worksheet = get_sheet(name, create=False)
        if worksheet is None:
            logger.warning("SHEET_AUTOCREATED:%s — worksheet dibuat otomatis", name)
            worksheet = get_sheet(name, create=True)
            end_col = _column_name(len(headers))
            _retry_google_call(
                worksheet.update,
                range_name=f"A1:{end_col}1",
                values=[headers],
            )
            _retry_google_call(worksheet.freeze, rows=1)
            with _cache_lock:
                _worksheet_cache[name] = worksheet

        if hasattr(worksheet, "col_count") and worksheet.col_count < len(headers):
            needed = len(headers) - worksheet.col_count
            _retry_google_call(worksheet.add_cols, needed)

        return worksheet
    except gspread.exceptions.APIError as exc:
        status = getattr(exc, "response", None)
        code = status.status_code if status else 0
        if code == 400:
            raise SheetRangeError(
                sheet=name,
                range_=f"{name}!A:Z",
                hint=(
                    f"Sheet '{name}' tidak ditemukan atau strukturnya kosong. "
                    "Jalankan `python -m app.setup_reference_structure --apply` "
                    "atau periksa nama sheet di spreadsheet."
                ),
            ) from exc
        raise


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
        if column == 0:
            cell_format["numberFormat"] = {"type": "DATE", "pattern": "dd mmm yyyy"}
            cell_format["horizontalAlignment"] = "CENTER"
        elif column == 4:
            cell_format["numberFormat"] = {"type": "NUMBER", "pattern": "#,##0"}
            cell_format["horizontalAlignment"] = "RIGHT"
        elif column in {5, 6, 7}:
            cell_format["numberFormat"] = {"type": "CURRENCY", "pattern": '"Rp" #,##0'}
            cell_format["horizontalAlignment"] = "RIGHT"
    return cell_format


def append_rows_atomically(
    rows_by_sheet: list[tuple[str, list]], *, _refreshed_stale_grid: bool = False,
) -> None:
    rows_by_sheet = list(rows_by_sheet)
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
        daily_detail_row_count = (
            len(worksheet.get_all_values())
            if sheet_name == "(2)Rincian Transaksi Harian"
            else None
        )
        is_unit_ledger = sheet_name in config.UNIT_USAHA
        unit_ledger_row_count = len(worksheet.get_all_values()) if is_unit_ledger else None
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

        if daily_detail_row_count is not None and rows:
            header_row_index = _detect_header_row_index(sheet_name, worksheet.get_all_values())
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": header_row_index,
                        "endRowIndex": header_row_index + 1,
                        "startColumnIndex": 7,
                        "endColumnIndex": 8,
                    },
                    "cell": {"userEnteredFormat": {
                        "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "bold": True,
                        },
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }},
                    "fields": "userEnteredFormat",
                }
            })
            appended_end_row = daily_detail_row_count + len(rows)
            requests.extend([
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": worksheet.id,
                            "startRowIndex": 5,
                            "endRowIndex": 6,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "destination": {
                            "sheetId": worksheet.id,
                            "startRowIndex": daily_detail_row_count,
                            "endRowIndex": appended_end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "pasteType": "PASTE_FORMAT",
                    }
                },
                {
                    "sortRange": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": 5,
                            "endRowIndex": appended_end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": 8,
                        },
                        "sortSpecs": [
                            {"dimensionIndex": 0, "sortOrder": "ASCENDING"},
                            {"dimensionIndex": 7, "sortOrder": "ASCENDING"},
                        ],
                    }
                },
            ])

        if unit_ledger_row_count is not None and rows:
            appended_end_row = unit_ledger_row_count + len(rows)
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": unit_ledger_row_count,
                        "endRowIndex": appended_end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": 9,
                    },
                    "cell": {"userEnteredFormat": {
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                        "borders": {"bottom": {
                            "style": "SOLID",
                            "color": {"red": 0.86, "green": 0.89, "blue": 0.92},
                        }},
                    }},
                    "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,userEnteredFormat.borders.bottom",
                }
            })

    try:
        _safe_append(
            lambda: spreadsheet.batch_update({"requests": requests}),
            [values for rows in rows_by_name.values() for values in rows],
            list(rows_by_name),
            "append_rows_atomically",
        )
    except Exception as error:
        if _refreshed_stale_grid or "no grid with id" not in str(error).casefold():
            raise
        for sheet_name in rows_by_name:
            _evict_sheet_cache(sheet_name)
        logger.warning(
            "Refreshing stale worksheet grid after append failure sheets=%s",
            list(rows_by_name),
        )
        try:
            return append_rows_atomically(rows_by_sheet, _refreshed_stale_grid=True)
        except Exception:
            raise error

    # Daily detail is kept chronological; unit ledgers are sorted by recap.
    for sheet_name in rows_by_name:
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


def _records_from_values(values: list[list], skip_subtotals: bool = False) -> list[dict]:
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

    description_index = next((
        index for index, header in enumerate(headers)
        if _normalize_header_label(header) in {"keterangan", "keterangan transaksi"}
    ), 2)

    records = []
    for row in values[header_row + 1:]:
        if not any(str(value).strip() for value in row):
            continue
        if skip_subtotals and description_index < len(row):
            keterangan = str(row[description_index]).strip().upper().lstrip("[")
            if keterangan.startswith(("SUBTOTAL MINGGU", "TOTAL BULAN")):
                continue

        records.append({
            header: numericise(row[index], default_blank="") if index < len(row) else ""
            for index, header in enumerate(headers)
        })
    return records


def _a1_column_range(sheet_name: str) -> str:
    return f"{_quote_sheet_name(sheet_name)}!A:Z"


def get_all_records_many(sheet_names: list[str], skip_subtotals: bool = False) -> dict[str, list[dict]]:
    with _cache_lock:
        now = monotonic()
        records_by_sheet = {}
        uncached_names = []
        for name in dict.fromkeys(sheet_names):
            # Cache is only used if skip_subtotals is False to avoid cache pollution
            # Or we could include skip_subtotals in cache key, but let's keep it simple for now
            # Actually, the existing cache doesn't support skip_subtotals.
            # I will skip cache if skip_subtotals is True for now to ensure correctness.
            if skip_subtotals:
                uncached_names.append(name)
                continue

            cached = _record_cache.get(name)
            if cached and cached[0] > now:
                records_by_sheet[name] = cached[1]
            else:
                uncached_names.append(name)

        if uncached_names:
            # BATCH OPT — gunakan _batch_get_ranges (1 request untuk N sheet)
            ranges = [_a1_column_range(name) for name in uncached_names]
            raw = _batch_get_ranges(ranges)
            for name, range_name in zip(uncached_names, ranges):
                values = raw.get(range_name, [])
                records = _records_from_values(values, skip_subtotals=skip_subtotals)
                if not skip_subtotals:
                    _record_cache[name] = (monotonic() + _get_ttl(name), records)
                records_by_sheet[name] = records

        return {
            name: [record.copy() for record in records_by_sheet.get(name, [])]
            for name in sheet_names
        }


def get_all_records(sheet_name: str, skip_subtotals: bool = False) -> list:
    return get_all_records_many([sheet_name], skip_subtotals=skip_subtotals)[sheet_name]


# ============================================================
# BATCH OPT #2 — find_transaction_ids baca kolom via batchGet.
# Untuk 5 sheet unit sekaligus = 1 request (bukan 5).
# Header lookup tetap pakai cache dari get_all_values.
# ============================================================
def find_transaction_ids(sheet_names: list[str], tx_ids: list[str]) -> dict[str, list[str]]:
    """Cari transaction ID yang sudah ada di sheet.

    BATCH OPT #2 — untuk N sheet, lakukan SATU batchGet berisi N range
    kolom Transaction ID, bukan N request terpisah.
    """
    requested_ids = {str(tx_id).strip() for tx_id in tx_ids if str(tx_id).strip()}
    found = {tx_id: [] for tx_id in requested_ids}
    if not requested_ids:
        return found

    # Langkah 1: cari indeks kolom Transaction ID per sheet (pakai cache header)
    sheets_to_query = []
    col_index_by_sheet = {}
    for sheet_name in dict.fromkeys(sheet_names):
        worksheet = get_sheet(sheet_name, create=False)
        if not worksheet:
            continue
        header_values = _cached_get_all_values(worksheet)
        if not header_values:
            continue
        header_row_index = _detect_header_row_index(sheet_name, header_values)
        if header_row_index >= len(header_values):
            continue
        header_row = header_values[header_row_index]
        tx_col_index = next(
            (i for i, h in enumerate(header_row)
             if str(h).strip().casefold() == "transaction id"),
            None,
        )
        if tx_col_index is None:
            continue
        sheets_to_query.append(sheet_name)
        col_index_by_sheet[sheet_name] = (tx_col_index, header_row_index)

    if not sheets_to_query:
        return found

    # Langkah 2: BATCH — baca semua kolom Transaction ID dalam 1 request.
    # Kolom per sheet bisa berbeda (karena perbedaan header), jadi
    # kelompokkan berdasarkan huruf kolom.
    grouped = {}
    for sheet_name in sheets_to_query:
        tx_col_index, _ = col_index_by_sheet[sheet_name]
        col_letter = _column_name(tx_col_index + 1)
        grouped.setdefault(col_letter, []).append(sheet_name)

    col_values_by_sheet = {}
    for col_letter, names in grouped.items():
        col_values_by_sheet.update(_batch_get_sheet_columns(names, col_letter))

    # Langkah 3: cocokkan ID
    for sheet_name in sheets_to_query:
        tx_col_index, header_row_index = col_index_by_sheet[sheet_name]
        col_values = col_values_by_sheet.get(sheet_name, [])
        start_row = header_row_index + 1
        for value in col_values[start_row:]:
            tx_id = str(value).strip()
            if tx_id in requested_ids and sheet_name not in found[tx_id]:
                found[tx_id].append(sheet_name)
    return found


def _transaction_id_column(worksheet, header_row_number):
    values = _cached_get_all_values(worksheet)
    header_index = header_row_number - 1
    while len(values) <= header_index:
        values.append([])
    headers = values[header_index]
    for column, header in enumerate(headers):
        if str(header).strip().casefold() == "transaction id":
            return column, values
    column = len(headers)
    _retry_google_call(worksheet.update_cell, header_row_number, column + 1, "Transaction ID")
    invalidate_cache(getattr(worksheet, "title", ""))
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
    values = _cached_get_all_values(metadata_sheet)
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
    try:
        from app.core.retry import error_code
        code = error_code(error) if error else None
    except (ImportError, AttributeError):
        response = getattr(error, "response", None) if error else None
        code = getattr(response, "status_code", None)

    details = json.dumps({
        "archived_at": archived_at,
        "missing_ids": sorted(missing_ids),
        "extra_ids": sorted(extra_ids),
        "error_type": type(error).__name__ if error else "",
        "error_code": code,
    }, sort_keys=True)
    logger.error("ARCHIVE_VERIFY_FAILED %s", details)
    try:
        from app.services.webhook_state import log_event
        log_event("system", "", "ARCHIVE_VERIFY_FAILED", error=details)
    except Exception as logging_error:
        logger.warning("Unable to persist archive verification failure log error_type=%s", type(logging_error).__name__)


# ============================================================
# BATCH OPT #3 — verifikasi arsip baca 3 kolom dalam 1 batchGet
# (bukan 3 col_values terpisah). Per attempt = 1 request.
# Juga kurangi attempt 4→3 dengan exponential backoff + jitter.
# ============================================================
def _read_archive_unit_ids(archive_worksheet, archived_at: str, unit: str, tx_col: int, archived_col: int):
    """Baca transaction ID untuk unit+archived_at tertentu.

    BATCH OPT #3 — 3 kolom (Unit, Tx ID, Diarsipkan) dibaca dalam
    1 batchGet request (bukan 3 col_values terpisah).
    """
    sheet_name = getattr(archive_worksheet, "title", "_ArsipTransaksi")
    quoted = _quote_sheet_name(sheet_name)
    unit_letter = _column_name(1)
    tx_letter = _column_name(tx_col + 1)
    archived_letter = _column_name(archived_col + 1)
    ranges = [
        f"{quoted}!{unit_letter}:{unit_letter}",
        f"{quoted}!{tx_letter}:{tx_letter}",
        f"{quoted}!{archived_letter}:{archived_letter}",
    ]
    try:
        raw = _batch_get_ranges(ranges)
        unit_col_vals = [r[0] if r else "" for r in raw.get(ranges[0], [])]
        tx_col_vals = [r[0] if r else "" for r in raw.get(ranges[1], [])]
        archived_col_vals = [r[0] if r else "" for r in raw.get(ranges[2], [])]
    except Exception:
        values = _retry_google_call(archive_worksheet.get_all_values)
        unit_col_vals = [row[0] if row else "" for row in values]
        tx_col_vals = [row[tx_col] if tx_col < len(row) else "" for row in values]
        archived_col_vals = [row[archived_col] if archived_col < len(row) else "" for row in values]

    ids = set()
    max_len = max(len(unit_col_vals), len(tx_col_vals), len(archived_col_vals))
    for idx in range(1, max_len):
        u = str(unit_col_vals[idx]).strip() if idx < len(unit_col_vals) else ""
        t = str(tx_col_vals[idx]).strip() if idx < len(tx_col_vals) else ""
        a = str(archived_col_vals[idx]).strip() if idx < len(archived_col_vals) else ""
        if u == unit and a == archived_at and t:
            ids.add(t)
    return ids


def archive_unit_transactions(
    rows_by_unit: dict[str, list[tuple[int, list]]],
    archived_at: str,
) -> dict:
    """Archive ledger rows from multiple unit sheets to *_ArsipTransaksi*.

    BATCH OPT #4 — untuk multiple unit:
    - Satu batchGet untuk baca semua ledger sumber sekaligus.
    - Satu batchGet untuk verifikasi (bukan per unit).
    - find_transaction_ids sudah pakai batch (BATCH OPT #2).
    """
    archive = _ensure_sheet_exists("_ArsipTransaksi", ARSIP_HEADERS)
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

    result: dict = {"archived": {}, "failed": {}, "skipped_delete": []}

    # BATCH OPT #4 — baca semua sheet sumber dalam SATU batchGet
    source_ranges = [
        f"{_quote_sheet_name(unit)}!A:Z" for unit in rows_by_unit
    ]
    source_values_by_unit = {}
    try:
        raw = _batch_get_ranges(source_ranges)
        for unit, range_name in zip(rows_by_unit, source_ranges):
            source_values_by_unit[unit] = raw.get(range_name, [])
    except Exception as exc:
        logger.warning("BATCH_READ_SOURCES_FAILED error=%s", type(exc).__name__)
        # Fallback: baca per sheet
        for unit in rows_by_unit:
            ws = get_sheet(unit, create=False)
            source_values_by_unit[unit] = ws.get_all_values() if ws else []

    for unit, rows in rows_by_unit.items():
        try:
            source = get_sheet(unit, create=False)
            if not source:
                raise ValueError(f"Worksheet sumber tidak ditemukan: {unit}")

            source_values = source_values_by_unit.get(unit, [])
            # Cari kolom Transaction ID di source (dari header yang sudah dibaca)
            source_header_idx = _detect_header_row_index(unit, source_values)
            if source_header_idx >= len(source_values):
                raise ValueError(f"Header sheet sumber tidak ditemukan: {unit}")
            source_header = source_values[source_header_idx]
            source_id_column = next(
                (i for i, h in enumerate(source_header)
                 if str(h).strip().casefold() == "transaction id"),
                None,
            )
            if source_id_column is None:
                # Tulis header Transaction ID jika belum ada
                source_id_column = len(source_header)
                _retry_google_call(
                    source.update_cell, source_header_idx + 1,
                    source_id_column + 1, "Transaction ID",
                )
                invalidate_cache(unit)

            unit_ids: list[str] = []
            delete_rows: list[tuple[int, list]] = []
            archived_rows: list[list] = []

            for row_number, provided_row in rows:
                current_row = source_values[row_number - 1] if row_number - 1 < len(source_values) else []
                if [str(v) for v in current_row] != [str(v) for v in provided_row]:
                    raise ArchiveVerificationError(
                        f"Baris sumber berubah: {unit} row {row_number}"
                    )
                padded = list(current_row[:9]) + [""] * max(0, 9 - len(current_row))
                transaction_id = _row_transaction_id(current_row, source_id_column)
                if not transaction_id:
                    transaction_id = _legacy_row_transaction_id(unit, row_number, padded)
                    _retry_google_call(source.update_cell, row_number, source_id_column + 1, transaction_id)
                    while len(current_row) <= source_id_column:
                        current_row.append("")
                    current_row[source_id_column] = transaction_id
                    source_values[row_number - 1] = current_row
                unit_ids.append(transaction_id)
                delete_rows.append((row_number, list(current_row)))
                archived_rows.append([unit, *padded, archived_at, transaction_id])

            if archived_rows:
                # Cek duplikat via before_ids (tanpa panggilan baca tambahan)
                rows_to_append = [
                    row for row in archived_rows
                    if row[-1] not in before_ids
                ]
                if rows_to_append:
                    _safe_append(
                        lambda r=rows_to_append: archive.append_rows(r, value_input_option="USER_ENTERED"),
                        rows_to_append,
                        ["_ArsipTransaksi"],
                        f"archive_unit_transactions_append:{unit}",
                    )

                # BATCH OPT #3 — verifikasi: 3 attempt, backoff+jitter, 1 batchGet/attempt
                expected_ids = set(unit_ids)
                found_ids: set[str] = set()
                missing_ids = expected_ids
                for _attempt in range(3):
                    if _attempt > 0:
                        import random as _random
                        sleep(min(1.0 * (2 ** _attempt) + _random.uniform(0, 0.5), 5.0))
                    invalidate_cache("_ArsipTransaksi")
                    found_ids = _read_archive_unit_ids(
                        archive, archived_at, unit, archive_id_column, archived_at_column,
                    )
                    missing_ids = expected_ids - found_ids
                    if not missing_ids:
                        break

                if missing_ids:
                    _log_archive_verification_failure(archived_at, missing_ids, set(), None)
                    raise ArchiveVerificationError(
                        f"Verifikasi arsip gagal untuk unit {unit} pada {archived_at}",
                        missing_ids,
                    )

                verified_at = datetime.now().astimezone().isoformat()
                _write_archive_manifest(
                    f"archive_manifest:{archived_at}",
                    {unit: {
                        "count": len(unit_ids),
                        "tx_ids_hash": _transaction_ids_hash(set(unit_ids)),
                        "verified_at": verified_at,
                    }},
                )

                for row_number, row_values in reversed(delete_rows):
                    _safe_delete_rows(source, row_number, row_values)
                invalidate_cache(unit)
                # Update before_ids untuk unit berikutnya di loop yang sama
                before_ids.update(unit_ids)

            result["archived"][unit] = len(archived_rows)

        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                "ARCHIVE_FAILED unit=%s archived_at=%s error_type=%s error=%s",
                unit, archived_at, type(exc).__name__, error_msg,
            )
            result["failed"][unit] = error_msg
            result["skipped_delete"].append(unit)

    invalidate_cache("_ArsipTransaksi")
    return result


def verify_archive_manifest(manifest_key: str) -> dict:
    manifest = _read_archive_manifest(manifest_key)
    archived_at = manifest_key.removeprefix("archive_manifest:")
    archive = get_sheet("_ArsipTransaksi", create=False)
    if not archive:
        raise ValueError("Worksheet _ArsipTransaksi tidak ditemukan")
    invalidate_cache("_ArsipTransaksi")
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
    invalidate_cache("_ArsipTransaksi")
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


@retry_google_api()
def find_subtotal_rows(sheet_name: str, label_prefix: str) -> list[int]:
    """Scan get_all_values() sekali, return 1-based row index
    di mana kolom Keterangan berawalan label_prefix."""
    worksheet = get_sheet(sheet_name, create=False)
    if not worksheet:
        return []
    values = _cached_get_all_values(worksheet)
    if not values:
        return []

    found = []
    header_index = _detect_header_row_index(sheet_name, values)
    header = values[header_index] if header_index < len(values) else []
    description_columns = [
        index for index, value in enumerate(header)
        if _normalize_header_label(value) in {"keterangan", "keterangan transaksi"}
    ]
    if not description_columns:
        description_columns = [2, 3]

    for idx, row in enumerate(values):
        if idx == header_index:
            continue
        if any(
            column < len(row)
            and str(row[column]).strip().upper().startswith(label_prefix.upper())
            for column in description_columns
        ):
                found.append(idx + 1)
    return found


@retry_google_api()
def insert_rows_after(sheet_name: str, after_row: int, rows: list[list]) -> None:
    """Insert after a physical 1-based worksheet row; blank rows are not renumbered."""
    worksheet = get_sheet(sheet_name)
    if not worksheet:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    if after_row < 0:
        raise ValueError("after_row must be a non-negative physical row index")
    if not rows:
        return
    worksheet.insert_rows(rows, row=after_row + 1)
    for offset, values in enumerate(rows):
        if any(
            str(value).strip().upper().startswith(("SUBTOTAL MINGGU", "TOTAL BULAN"))
            for value in values
        ):
            format_total_row(sheet_name, after_row + offset + 1)
    invalidate_cache(sheet_name)


def format_total_row(sheet_name: str, row_index: int) -> None:
    """Apply consistent styling to a weekly or monthly total row."""
    worksheet = get_sheet(sheet_name, create=False)
    if not worksheet:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    values = _retry_google_call(worksheet.row_values, row_index)
    label = next((
        str(value).strip().upper()
        for value in values
        if str(value).strip().upper().startswith(("SUBTOTAL MINGGU", "TOTAL BULAN"))
    ), "")
    if not label:
        return

    is_daily_detail = sheet_name == "(2)Rincian Transaksi Harian"
    # Ledger unit layout (10 kolom):
    #   0=Nomor, 1=Tanggal, 2=Nama Barang, 3=Keterangan, 4=Kuantitas,
    #   5=Satuan, 6=Pemasukan, 7=Pengeluaran, 8=Sisa Saldo, 9=Transaction ID
    # Rincian Harian layout (8 kolom):
    #   0=Tanggal, 1=Nama Produk, 2=Keterangan, 3=Kuantitas,
    #   4=Satuan, 5=Pemasukan, 6=Pengeluaran, 7=Transaction ID
    if is_daily_detail:
        end_column_index = 8
        label_column_index = 2
        currency_start, currency_end = 5, 7
    else:
        end_column_index = 9
        label_column_index = 3
        currency_start, currency_end = 6, 9

    background = (
        {"red": 0.82, "green": 0.91, "blue": 0.84}
        if label.startswith("TOTAL BULAN")
        else {"red": 1.0, "green": 0.94, "blue": 0.72}
    )
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": row_index - 1,
                    "endRowIndex": row_index,
                    "startColumnIndex": 0,
                    "endColumnIndex": end_column_index,
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": background,
                    "textFormat": {"bold": True},
                    "wrapStrategy": "CLIP",
                }},
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": row_index - 1,
                    "endRowIndex": row_index,
                    "startColumnIndex": label_column_index,
                    "endColumnIndex": label_column_index + 1,
                },
                "cell": {"userEnteredFormat": {
                    "horizontalAlignment": "LEFT",
                    "wrapStrategy": "CLIP",
                }},
                "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.wrapStrategy",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": row_index - 1,
                    "endRowIndex": row_index,
                    "startColumnIndex": currency_start,
                    "endColumnIndex": currency_end,
                },
                "cell": {"userEnteredFormat": {
                    "horizontalAlignment": "RIGHT",
                    "numberFormat": {"type": "CURRENCY", "pattern": '"Rp" #,##0'},
                }},
                "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.numberFormat",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": label_column_index,
                    "endIndex": label_column_index + 1,
                },
                "properties": {"pixelSize": 360},
                "fields": "pixelSize",
            }
        },
    ]
    _retry_google_call(worksheet.spreadsheet.batch_update, {"requests": requests})
    invalidate_cache(sheet_name)

@retry_google_api()
def rewrite_period_block(sheet_name: str, start_row: int, end_row: int, new_rows: list[list]) -> None:
    """Ganti blok [start_row, end_row] dengan new_rows via
    spreadsheets.batchUpdate (atomik). Invalidasi cache.
    start_row dan end_row adalah 1-based index."""
    if not new_rows:
        return
    if len(new_rows) != (end_row - start_row + 1):
        raise ValueError(f"Mismatch row count: new={len(new_rows)} vs range={end_row-start_row+1}")

    width = max(len(row) for row in new_rows)
    spreadsheet = get_spreadsheet()

    def write_block(worksheet):
        if not worksheet:
            raise ValueError(f"Worksheet not found: {sheet_name}")
        rows = []
        for row in new_rows:
            cells = []
            for value in row + [""] * (width - len(row)):
                if isinstance(value, bool):
                    entered_value = {"boolValue": value}
                elif isinstance(value, (int, float)):
                    entered_value = {"numberValue": value}
                else:
                    entered_value = {"stringValue": str(value or "")}
                cells.append({"userEnteredValue": entered_value})
            rows.append({"values": cells})
        body = {"requests": [{
            "updateCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": width,
                },
                "rows": rows,
                "fields": "userEnteredValue",
            }
        }]}
        return _retry_google_call(spreadsheet.batch_update, body)

    worksheet = get_sheet(sheet_name)
    if not worksheet:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    try:
        write_block(worksheet)
    except Exception as original_error:
        if "no grid with id" not in str(original_error).casefold():
            raise
        _evict_sheet_cache(sheet_name)
        logger.warning("Refreshing stale worksheet grid after block rewrite sheet=%s", sheet_name)
        refreshed = get_sheet(sheet_name, create=False)
        if not refreshed:
            raise original_error
        try:
            write_block(refreshed)
        except Exception:
            raise original_error
    invalidate_cache(sheet_name)


@retry_google_api()
def delete_rows_bottom_up(sheet_name: str, row_indices: list[int]) -> int:
    """Hapus dari index terbesar ke terkecil. Return jumlah dihapus."""
    worksheet = get_sheet(sheet_name)
    if not worksheet or not row_indices:
        return 0

    sorted_indices = sorted(row_indices, reverse=True)
    count = 0
    for idx in sorted_indices:
        worksheet.delete_rows(idx)
        count += 1

    if count > 0:
        invalidate_cache(sheet_name)
    return count