import calendar
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock
from typing import Literal

from app.config import config
from app.core.utils import format_tanggal_id, now_tz, parse_amount, parse_date
from app.repositories import sheet_repo

logger = logging.getLogger(__name__)
_PERIOD_LOCK = RLock()

@dataclass
class Period:
    label: str
    start_date: date
    end_date: date
    kind: Literal["week", "month"]

@dataclass
class PeriodTotal:
    period: Period
    pemasukan: int
    pengeluaran: int
    sisa_saldo_akhir: int

def get_period_boundaries(year: int, month: int) -> list[Period]:
    """Return 5 Period: 4 minggu + 1 bulan (kind='month').
    Minggu 1: tgl 1-7
    Minggu 2: tgl 8-14
    Minggu 3: tgl 15-21
    Minggu 4: tgl 22-akhir bulan
    """
    _, last_day = calendar.monthrange(year, month)
    
    # Generate labels using format_tanggal_id
    # dummy_date for format_tanggal_id which expects datetime
    def get_month_name(y, m):
        dt = datetime(y, m, 1)
        return format_tanggal_id(dt).split()[-2] # Extract month name from "01 Januari 2024"

    month_label = get_month_name(year, month)
    year_str = str(year)

    periods = []
    
    # Weeks
    week_configs = [
        (1, 7, 1),
        (8, 14, 2),
        (15, 21, 3),
        (22, last_day, 4)
    ]
    
    for start, end, idx in week_configs:
        label = f"SUBTOTAL MINGGU {idx} ({start:02d}-{end:02d} {month_label} {year_str})"
        periods.append(Period(
            label=label,
            start_date=date(year, month, start),
            end_date=date(year, month, end),
            kind="week"
        ))
        
    # Month
    monthly_label = f"TOTAL BULAN {month_label} {year_str}"
    periods.append(Period(
        label=monthly_label,
        start_date=date(year, month, 1),
        end_date=date(year, month, last_day),
        kind="month"
    ))
    
    return periods

def get_period_for_date(target: date) -> Period:
    """Return Period (minggu) yang mencakup tanggal tsb."""
    boundaries = get_period_boundaries(target.year, target.month)
    for p in boundaries:
        if p.kind == "week" and p.start_date <= target <= p.end_date:
            return p
    return boundaries[3] # Fallback to week 4 if somehow not found

def is_subtotal_row(row: list) -> bool:
    """Recognize subtotal labels in current and legacy ledger layouts."""
    return any(
        _clean_subtotal_label(value).upper().startswith(("SUBTOTAL MINGGU", "TOTAL BULAN"))
        for value in row
    )

def _clean_subtotal_label(value) -> str:
    return str(value or "").strip().strip("[]").strip()

def _subtotal_label(row: list, description_index: int | None) -> str:
    return next((
        _clean_subtotal_label(value)
        for value in row
        if _clean_subtotal_label(value).upper().startswith(("SUBTOTAL MINGGU", "TOTAL BULAN"))
    ), "")

def _column_indices(sheet_name: str, values: list[list]) -> dict[str, int | None]:
    header_index = sheet_repo._detect_header_row_index(sheet_name, values)
    header = values[header_index] if 0 <= header_index < len(values) else []
    normalized = [sheet_repo._normalize_header_label(cell) for cell in header]
    has_header = any(value in {"tanggal", "tgl", "keterangan", "keterangan transaksi"} for value in normalized)
    if not has_header:
        header_index = -1
        normalized = []

    def find_column(*labels):
        return next((index for index, value in enumerate(normalized) if value in labels), None)

    is_daily = sheet_name == "(2)Rincian Transaksi Harian"
    return {
        "header": header_index,
        "date": find_column("tanggal", "tgl") if normalized else 0,
        "income": find_column("pemasukan (rp)", "pemasukan") if normalized else None,
        "expense": find_column("pengeluaran (rp)", "pengeluaran") if normalized else None,
        "balance": find_column("sisa saldo", "saldo") if normalized else None,
        "description": find_column("keterangan transaksi", "keterangan"),
        "tx_id": find_column("transaction id"),
        "daily": is_daily,
    }

def _resolved_columns(sheet_name: str, values: list[list]) -> dict[str, int | None]:
    columns = _column_indices(sheet_name, values)
    if columns["income"] is None:
        columns["income"] = 5
    if columns["expense"] is None:
        columns["expense"] = 6
    if columns["balance"] is None and not columns["daily"]:
        columns["balance"] = 7
    if columns["description"] is None:
        columns["description"] = 2
    if columns["tx_id"] is None:
        columns["tx_id"] = 7 if columns["daily"] else 8
    return columns

def _parse_ledger_date(value):
    parsed = parse_date(value)
    if parsed:
        return parsed
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    return None

def _row_transaction_date(row: list, date_index: int) -> date | None:
    parsed = _parse_ledger_date(row[date_index] if len(row) > date_index else "")
    if parsed:
        return parsed
    for index in range(date_index + 1, len(row)):
        if any(str(value).strip() for value in row[date_index:index]):
            break
        parsed = _parse_ledger_date(row[index])
        if parsed:
            return parsed
    return None

def compute_period_total(sheet_name: str, period: Period) -> PeriodTotal:
    """
    - Baca get_all_values() sekali
    - Skip is_subtotal_row
    - Filter tanggal dalam [period.start_date, period.end_date]
    - Jumlahkan Pemasukan (kolom 6) dan Pengeluaran (kolom 7)
    - sisa_saldo_akhir = Sisa Saldo (kolom 8) baris transaksi
      terakhir dalam periode; jika periode kosong, saldo terakhir
      sebelum period.start_date; jika sheet kosong, 0
    """
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return PeriodTotal(period, 0, 0, 0)
    
    values = worksheet.get_all_values()
    if not values:
        return PeriodTotal(period, 0, 0, 0)
    
    columns = _resolved_columns(sheet_name, values)
    total_pemasukan = 0
    total_pengeluaran = 0
    last_saldo = 0
    found_in_period = False
    last_saldo_before = 0
    last_before_date = None
    last_period_date = None
    
    for row_index, row in enumerate(values):
        if row_index <= columns["header"]:
            continue
        if is_subtotal_row(row):
            continue

        date_index = columns["date"] or 0
        row_date = _row_transaction_date(row, date_index)
        if not row_date:
            continue

        income_index = columns["income"]
        expense_index = columns["expense"]
        balance_index = columns["balance"]
        row_pemasukan = parse_amount(row[income_index]) if income_index is not None and len(row) > income_index else 0
        row_pengeluaran = parse_amount(row[expense_index]) if expense_index is not None and len(row) > expense_index else 0
        row_saldo = parse_amount(row[balance_index]) if balance_index is not None and len(row) > balance_index else 0
        
        if row_date < period.start_date:
            if last_before_date is None or row_date >= last_before_date:
                last_saldo_before = row_saldo
                last_before_date = row_date
        elif period.start_date <= row_date <= period.end_date:
            total_pemasukan += row_pemasukan
            total_pengeluaran += row_pengeluaran
            found_in_period = True
            if last_period_date is None or row_date >= last_period_date:
                last_saldo = row_saldo
                last_period_date = row_date
            
    if not found_in_period:
        last_saldo = last_saldo_before
        
    return PeriodTotal(period, int(total_pemasukan), int(total_pengeluaran), int(last_saldo))

def ensure_sorted(sheet_name: str, period: Period) -> bool:
    """
    Sort transaction rows within the period block by date, tie-break by
    Transaction ID. Preserve physical slots of non-block rows (subtotal).
    Return True if any change was written.
    """
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return False

    values = worksheet.get_all_values()
    if not values:
        return False

    columns = _resolved_columns(sheet_name, values)
    block_indices = []
    block_rows = []

    for idx, row in enumerate(values):
        if idx <= columns["header"]:
            continue
        if is_subtotal_row(row):
            continue

        date_index = columns["date"] if columns["date"] is not None else 0
        row_date = _row_transaction_date(row, date_index)
        if row_date and period.start_date <= row_date <= period.end_date:
            block_indices.append(idx + 1)
            block_rows.append(row)

    if not block_rows:
        return False

    def sort_key(row):
        date_index = columns["date"] if columns["date"] is not None else 0
        d = _row_transaction_date(row, date_index)
        tx_id_idx = columns["tx_id"]
        t = str(row[tx_id_idx]).strip() if tx_id_idx is not None and tx_id_idx < len(row) else ""
        return (d or date(9999, 12, 31), t)

    sorted_rows = sorted(block_rows, key=sort_key)

    if sorted_rows == block_rows:
        return False

    start_row = min(block_indices)
    end_row = max(block_indices)

    if len(block_indices) != (end_row - start_row + 1):
        replacement = [list(row) for row in values[start_row - 1:end_row]]
        sorted_iter = iter(sorted_rows)
        for original_idx in block_indices:
            offset = original_idx - start_row
            if 0 <= offset < len(replacement):
                replacement[offset] = next(sorted_iter)
        sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, replacement)
    else:
        sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, sorted_rows)

    return True

def write_period_total(sheet_name: str, period: Period, force: bool = False) -> PeriodTotal:
    """
    Replace the matching subtotal at the period boundary. Empty weeks get
    a zero subtotal only when the ledger contains transactions elsewhere.
    Jika force=True, guard tanggal dilewati (untuk update real-time minggu berjalan).
    """
    with _PERIOD_LOCK:
        if not force and now_tz().date() < period.end_date:
            return compute_period_total(sheet_name, period)
        ensure_sorted(sheet_name, period)
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        values = worksheet.get_all_values() if worksheet else []
        columns = _resolved_columns(sheet_name, values)
        description_index = columns["description"]
        old_rows = []
        for index, row in enumerate(values):
            if index <= columns["header"] or description_index is None:
                continue
            label = _subtotal_label(row, description_index)
            if label == period.label:
                old_rows.append(index + 1)

        to_delete = old_rows
        if to_delete:
            sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)

        total = compute_period_total(sheet_name, period)
        values = worksheet.get_all_values() if worksheet else []
        latest_date, insert_after = _latest_transaction_position(sheet_name, values, period)
        if latest_date is None:
            if not _has_transaction_rows(sheet_name, values):
                return total
            insert_after = _last_row_before_period(sheet_name, values, period)
        if insert_after is None or insert_after < columns["header"] + 1:
            insert_after = columns["header"] + 1

        new_row = _build_total_row(sheet_name, period, total)
        sheet_repo.insert_rows_after(sheet_name, insert_after, [new_row])
        return total

def _build_total_row(sheet_name: str, period: Period, total: PeriodTotal) -> list:
    """
    Bangun baris total sesuai layout header masing-masing sheet.
    Ledger unit: Nomor, Tanggal, Nama Barang, Keterangan, Kuantitas, Satuan,
                 Pemasukan, Pengeluaran, Sisa Saldo, Transaction ID (10 kolom).
    Rincian Harian: Tanggal, Nama Produk, Keterangan, Kuantitas, Satuan,
                    Pemasukan, Pengeluaran, Transaction ID (8 kolom).
    """
    if sheet_name == "(2)Rincian Transaksi Harian":
        return [
            "", "", period.label, "", "",
            total.pemasukan, total.pengeluaran, "",
        ]
    return [
        "",
        period.end_date.strftime("%Y-%m-%d"),
        "",
        period.label,
        "", "",
        total.pemasukan,
        total.pengeluaran,
        total.pemasukan - total.pengeluaran,
        "",
    ]

def _latest_transaction_position(sheet_name: str, values: list[list], period: Period):
    columns = _resolved_columns(sheet_name, values)
    date_index = columns["date"] if columns["date"] is not None else 0
    dated_rows = []
    for index, row in enumerate(values):
        if index <= columns["header"] or is_subtotal_row(row):
            continue
        row_date = _row_transaction_date(row, date_index)
        if row_date and period.start_date <= row_date <= period.end_date:
            dated_rows.append((row_date, index + 1))
    if not dated_rows:
        return None, None
    latest_date = max(row_date for row_date, _ in dated_rows)
    latest_row = max(row_number for row_date, row_number in dated_rows if row_date == latest_date)
    return latest_date, latest_row


def _has_transaction_rows(sheet_name: str, values: list[list]) -> bool:
    columns = _resolved_columns(sheet_name, values)
    date_index = columns["date"] if columns["date"] is not None else 0
    return any(
        index > columns["header"]
        and not is_subtotal_row(row)
        and _row_transaction_date(row, date_index) is not None
        for index, row in enumerate(values)
    )


def _last_row_before_period(sheet_name: str, values: list[list], period: Period) -> int:
    columns = _resolved_columns(sheet_name, values)
    date_index = columns["date"] if columns["date"] is not None else 0
    last_row = columns["header"] + 1
    for index, row in enumerate(values):
        if index <= columns["header"]:
            continue
        if is_subtotal_row(row):
            last_row = index + 1
            continue
        row_date = _row_transaction_date(row, date_index)
        if row_date and row_date >= period.start_date:
            break
        if row_date:
            last_row = index + 1
    return max(0, last_row)

def sort_all_transactions(sheet_name: str) -> bool:
    """Sort dated transaction rows globally while preserving their physical slots."""
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return False
    values = worksheet.get_all_values()
    if not values:
        return False

    columns = _resolved_columns(sheet_name, values)
    date_index = columns["date"] if columns["date"] is not None else 0
    transaction_slots = []
    transaction_rows = []
    for index, row in enumerate(values):
        if index <= columns["header"] or is_subtotal_row(row):
            continue
        row_date = _row_transaction_date(row, date_index)
        if row_date:
            transaction_slots.append(index + 1)
            transaction_rows.append(row)

    tx_id_index = columns["tx_id"]
    sorted_rows = sorted(
        transaction_rows,
        key=lambda row: (
            _row_transaction_date(row, date_index) or date(9999, 12, 31),
            str(row[tx_id_index]).strip() if tx_id_index is not None and len(row) > tx_id_index else "",
        ),
    )
    if sorted_rows == transaction_rows:
        return False

    start_row, end_row = transaction_slots[0], transaction_slots[-1]
    replacement = [list(row) for row in values[start_row - 1:end_row]]
    for slot, row in zip(transaction_slots, sorted_rows):
        replacement[slot - start_row] = row
    sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, replacement)
    return True

def normalize_sheet_rows(sheet_name: str) -> int:
    """Place transaction fields in the canonical columns and trim overflow cells."""
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return 0
    values = worksheet.get_all_values()
    if not values:
        return 0

    columns = _resolved_columns(sheet_name, values)
    header_index = columns["header"]
    if header_index < 0 or header_index >= len(values):
        return 0
    header = values[header_index]
    width = len(header)
    if width == 0 or header_index + 1 >= len(values):
        return 0

    normalized_header = [sheet_repo._normalize_header_label(cell) for cell in header]

    def find_source(*labels, fallback=None):
        return next(
            (index for index, value in enumerate(normalized_header) if value in labels),
            fallback,
        )

    source_indices = {
        "date": columns["date"] if columns["date"] is not None else 0,
        "name": find_source("nama barang/jasa /produk", "nama produk /jasa/ barang", "nama produk", "nama barang", "produk", "barang", fallback=1),
        "description": columns["description"] if columns["description"] is not None else 2,
        "quantity": find_source("kuantitas", "qty", fallback=3),
        "unit": find_source("satuan", "satuan (rp)", fallback=4),
        "income": columns["income"] if columns["income"] is not None else 5,
        "expense": columns["expense"] if columns["expense"] is not None else 6,
        "balance": columns["balance"],
        "tx_id": columns["tx_id"],
    }
    daily = columns["daily"]
    target_indices = {
        "date": 0, "name": 1, "description": 2, "quantity": 3,
        "unit": 4, "income": 5, "expense": 6,
        "balance": 7, "tx_id": 8,
    }
    if daily:
        target_indices["balance"] = None
        target_indices["tx_id"] = 7
    normalized_rows = []
    changed_count = 0
    for original in values[header_index + 1:]:
        row = list(original)
        if is_subtotal_row(row):
            normalized = row[:width] + [""] * max(0, width - len(row))
        else:
            expected_date_index = source_indices["date"]
            expected_date = _parse_ledger_date(
                row[expected_date_index] if len(row) > expected_date_index else ""
            )
            detected_date_index = next((
                index for index, value in enumerate(row)
                if _parse_ledger_date(value) is not None
            ), expected_date_index)
            shift = max(0, detected_date_index - expected_date_index) if not expected_date else 0
            if shift:
                row = row[shift:]
            row_date = expected_date or _parse_ledger_date(
                row[expected_date_index] if len(row) > expected_date_index else ""
            )
            if row_date:
                normalized = [""] * width
                for field, target_index in target_indices.items():
                    source_index = source_indices[field]
                    if target_index is None or source_index is None:
                        continue
                    if source_index < len(row) and target_index < width:
                        normalized[target_index] = row[source_index]
                normalized[0] = row_date.isoformat()
                tx_id_index = target_indices["tx_id"]
                if tx_id_index < width and not normalized[tx_id_index]:
                    transaction_id = next((
                        str(value).strip() for value in row
                        if re.fullmatch(r"\d{8}-[A-Z]{3}-[A-Fa-f0-9]{8}", str(value).strip())
                    ), "")
                    normalized[tx_id_index] = transaction_id
            else:
                normalized = row[:width] + [""] * max(0, width - len(row))

        if normalized != original:
            changed_count += 1
        normalized_rows.append(normalized)

    if changed_count:
        sheet_repo.rewrite_period_block(
            sheet_name, header_index + 2, len(values), normalized_rows,
        )
    return changed_count


def normalize_row_formatting(sheet_name: str) -> int:
    """Backward-compatible name for transaction row normalization."""
    return normalize_sheet_rows(sheet_name)


def write_monthly_total(sheet_name: str, year: int, month: int, force: bool = False) -> PeriodTotal:
    """
    Replace the monthly total immediately after the week-4 subtotal, or after
    the month's final transaction when no week-4 subtotal exists.
    Jika force=True, guard tanggal dilewati.
    """
    with _PERIOD_LOCK:
        boundaries = get_period_boundaries(year, month)
        monthly_period = next(p for p in boundaries if p.kind == "month")
        if not force and now_tz().date() < monthly_period.end_date:
            return compute_period_total(sheet_name, monthly_period)
        week4_period = boundaries[3]
        ensure_sorted(sheet_name, monthly_period)
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if not worksheet:
            return PeriodTotal(monthly_period, 0, 0, 0)
        values = worksheet.get_all_values() if worksheet else []
        columns = _resolved_columns(sheet_name, values)
        description_index = columns["description"]
        to_delete = [
            index + 1 for index, row in enumerate(values)
            if index > columns["header"] and description_index is not None
            and _subtotal_label(row, description_index) == monthly_period.label
        ]
        if to_delete:
            sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)

        total = compute_period_total(sheet_name, monthly_period)
        values = worksheet.get_all_values() if worksheet else []
        columns = _resolved_columns(sheet_name, values)
        description_index = columns["description"]
        latest_date, latest_transaction_row = _latest_transaction_position(
            sheet_name, values, monthly_period,
        )
        if latest_date is None:
            if _has_transaction_rows(sheet_name, values):
                latest_transaction_row = _last_row_before_period(sheet_name, values, monthly_period)
            else:
                latest_transaction_row = columns["header"] + 1
        if latest_transaction_row is None or latest_transaction_row < columns["header"] + 1:
            latest_transaction_row = columns["header"] + 1

        week4_total_rows = [
            index + 1 for index, row in enumerate(values)
            if index > columns["header"]
            and _subtotal_label(row, description_index) == week4_period.label
        ]
        insert_after = max(week4_total_rows) if week4_total_rows else latest_transaction_row
        if insert_after is None or insert_after < columns["header"] + 1:
            insert_after = columns["header"] + 1
        new_row = _build_total_row(sheet_name, monthly_period, total)
        sheet_repo.insert_rows_after(sheet_name, insert_after, [new_row])
        return total


def process_all_units_period(year: int, month: int,
                             period_kind: Literal["week", "month"] = "week",
                             week_index: int | None = None,
                             force: bool = False,
                             unit_names: list[str] | None = None) -> dict:
    """Loop 5 unit ledger, panggil write_period_total / 
    write_monthly_total. Return dict {unit: PeriodTotal}."""
    results = {}
    boundaries = get_period_boundaries(year, month)
    
    if period_kind == "month":
        target_period = next(p for p in boundaries if p.kind == "month")
    else:
        if week_index is None:
            raise ValueError("week_index is required for week period")
        target_period = boundaries[week_index - 1]
        
    units = unit_names if unit_names is not None else config.LEDGER_UNITS
    for unit in units:
        try:
            if period_kind == "month":
                res = write_monthly_total(unit, year, month, force=force)
            else:
                res = write_period_total(unit, target_period, force=force)
            results[unit] = res
        except Exception as e:
            logger.error(f"Failed to process periodic total for unit {unit}: {e}")
            results[unit] = e
            
    return results
