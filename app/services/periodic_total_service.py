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
        str(value).strip().upper().startswith(("SUBTOTAL MINGGU", "TOTAL BULAN"))
        for value in row
    )

def _subtotal_label(row: list, description_index: int | None) -> str:
    return next((
        str(value).strip()
        for value in row
        if str(value).strip().upper().startswith(("SUBTOTAL MINGGU", "TOTAL BULAN"))
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
        columns["tx_id"] = 8 if not columns["daily"] else 7
    return columns

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
        row_date = parse_date(row[date_index] if len(row) > date_index else "")
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
    - Baca semua values + row index (1-based)
    - Identifikasi blok: row index dengan tanggal dalam period,
      bukan is_subtotal_row
    - Cek ascending berdasarkan Tanggal, tie-break Transaction ID
    - Jika sudah urut -> return False
    - Jika belum -> sort di Python, tulis ulang HANYA baris
      dalam blok pada posisi row index SAMA via rewrite_period_block,
      return True
    """
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return False
        
    values = worksheet.get_all_values()
    if not values:
        return False
        
    columns = _resolved_columns(sheet_name, values)
    block_indices = [] # 1-based physical row indices
    block_rows = []
    
    for idx, row in enumerate(values):
        if idx <= columns["header"]:
            continue
        if is_subtotal_row(row):
            continue

        date_index = columns["date"] if columns["date"] is not None else 0
        row_date = parse_date(row[date_index] if len(row) > date_index else "")
        if row_date and period.start_date <= row_date <= period.end_date:
            block_indices.append(idx + 1)
            block_rows.append(row)
            
    if not block_rows:
        return False
        
    def sort_key(row):
        date_index = columns["date"] if columns["date"] is not None else 0
        d = parse_date(row[date_index] if len(row) > date_index else "")
        tx_id_idx = columns["tx_id"]
        t = str(row[tx_id_idx]).strip() if tx_id_idx is not None and tx_id_idx < len(row) else ""
        return (d or date(9999, 12, 31), t)

    sorted_rows = sorted(block_rows, key=sort_key)
    
    if sorted_rows == block_rows:
        return False
        
    # Write back the sorted block
    # We assume block_indices are contiguous or at least we should replace them at their original positions.
    # The requirement says "tulis ulang HANYA baris dalam blok pada posisi row index SAMA".
    # If the block is not contiguous, rewrite_period_block as defined might not work directly if it expects a single range.
    # However, for periodic totals, the block *should* be mostly contiguous.
    # If it's not contiguous, we'd need multiple update calls.
    # But rewrite_period_block takes start_row and end_row.
    # Let's assume for now the block is contiguous for simplicity as it's a "period block".
    
    start_row = min(block_indices)
    end_row = max(block_indices)
    
    # If not contiguous, we have a problem with rewrite_period_block.
    # Let's check if it's contiguous.
    if len(block_indices) != (end_row - start_row + 1):
        # Not contiguous. We should update one by one or in chunks.
        # But rewrite_period_block is designed for a single block.
        # Let's just update the whole range from start_row to end_row, 
        # but we must preserve rows that are not in our block (like subtotal rows).
        new_values = list(values[start_row-1:end_row])
        for i, original_idx in enumerate(block_indices):
            new_values[original_idx - start_row] = sorted_rows[i]
        
        sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, new_values)
    else:
        sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, sorted_rows)
        
    return True

def write_period_total(sheet_name: str, period: Period) -> PeriodTotal:
    """
    - ensure_sorted dulu
    - find_subtotal_rows(sheet_name, label_prefix=period.label[:20])
      -> hapus via delete_rows_bottom_up
    - compute_period_total (sekarang bersih)
    - Bangun baris 9 kolom:
      [Nomor="", Tanggal=period.end_date, Nama Barang="", 
       Keterangan=period.label, Kuantitas="", Satuan="", 
       Pemasukan=total.pemasukan, Pengeluaran=total.pengeluaran, 
       Sisa Saldo=total.sisa_saldo_akhir]
    - Posisi insert: tepat setelah transaksi terakhir dalam period;
      jika kosong, setelah baris terakhir sebelum period.start_date
    - insert_rows_after
    - Return PeriodTotal
    """
    with _PERIOD_LOCK:
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
        columns = _resolved_columns(sheet_name, values)
        date_index = columns["date"] if columns["date"] is not None else 0
        last_transaction_row = None
        last_prior_row = None
        first_future_row = None
        previous_week_rows = []
        week_index = None
        month_year_label = format_tanggal_id(datetime(period.start_date.year, period.start_date.month, 1)).split()[-2:]
        month_year_text = " ".join(month_year_label)
        for index, row in enumerate(values):
            if index <= columns["header"]:
                continue
            label = _subtotal_label(row, columns["description"])
            match = re.match(r"SUBTOTAL MINGGU\s+(\d+)", label.upper())
            if match and month_year_text.casefold() in label.casefold():
                if week_index is None:
                    period_match = re.match(r"SUBTOTAL MINGGU\s+(\d+)", period.label.upper())
                    week_index = int(period_match.group(1)) if period_match else None
                if week_index is not None and int(match.group(1)) < week_index:
                    previous_week_rows.append(index + 1)

        for index, row in enumerate(values):
            if index <= columns["header"] or is_subtotal_row(row):
                continue
            row_date = parse_date(row[date_index] if len(row) > date_index else "")
            if row_date and period.start_date <= row_date <= period.end_date:
                last_transaction_row = index + 1
            elif row_date and row_date < period.start_date:
                last_prior_row = index + 1
            elif row_date and row_date > period.end_date:
                first_future_row = min(first_future_row or index + 1, index + 1)

        header_row = columns["header"] + 1
        if last_transaction_row is not None:
            insert_after = last_transaction_row
        else:
            prior_anchor = max([header_row, *(previous_week_rows), *( [last_prior_row] if last_prior_row else [])])
            insert_after = min(prior_anchor, first_future_row - 1) if first_future_row else prior_anchor
        new_row = _build_total_row(sheet_name, period, total)
        sheet_repo.insert_rows_after(sheet_name, insert_after, [new_row])
        return total

def _build_total_row(sheet_name: str, period: Period, total: PeriodTotal) -> list:
    if sheet_name == "(2)Rincian Transaksi Harian":
        return [
            "", "", "", "", period.label,
            total.pemasukan, total.pengeluaran, "",
        ]
    return [
        period.end_date.strftime("%Y-%m-%d"), "", period.label,
        "", "", total.pemasukan, total.pengeluaran, total.sisa_saldo_akhir, "",
    ]

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
        row_date = parse_date(row[date_index] if len(row) > date_index else "")
        if row_date:
            transaction_slots.append(index + 1)
            transaction_rows.append(row)

    tx_id_index = columns["tx_id"]
    sorted_rows = sorted(
        transaction_rows,
        key=lambda row: (
            parse_date(row[date_index]) or date(9999, 12, 31),
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

def write_monthly_total(sheet_name: str, year: int, month: int) -> PeriodTotal:
    """
    - Hapus TOTAL BULAN lama untuk bulan itu (replace)
    - Hitung agregat: jumlahkan pemasukan/pengeluaran dari
      transaksi dalam bulan (BUKAN jumlah baris subtotal)
    - sisa_saldo_akhir = saldo transaksi terakhir bulan
    - Insert TEPAT SETELAH subtotal minggu 4
      (atau setelah transaksi terakhir bulan jika minggu 4 kosong)
    - Return PeriodTotal kind="month"
    """
    with _PERIOD_LOCK:
        boundaries = get_period_boundaries(year, month)
        monthly_period = next(p for p in boundaries if p.kind == "month")
        week4_period = boundaries[3]
        ensure_sorted(sheet_name, monthly_period)
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
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
        date_index = columns["date"] if columns["date"] is not None else 0
        description_index = columns["description"]
        month_year_label = monthly_period.label.removeprefix("TOTAL BULAN ")
        week4_row = None
        latest_weekly_row = None
        last_transaction_row = None
        for index, row in enumerate(values):
            if index <= columns["header"]:
                continue
            label = _subtotal_label(row, description_index)
            if label == week4_period.label:
                week4_row = index + 1
            if label.startswith("SUBTOTAL MINGGU") and month_year_label in label:
                latest_weekly_row = index + 1
            if is_subtotal_row(row):
                continue
            row_date = parse_date(row[date_index] if len(row) > date_index else "")
            if row_date and monthly_period.start_date <= row_date <= monthly_period.end_date:
                last_transaction_row = index + 1

        insert_after = week4_row or latest_weekly_row or last_transaction_row or columns["header"] + 1
        new_row = _build_total_row(sheet_name, monthly_period, total)
        sheet_repo.insert_rows_after(sheet_name, insert_after, [new_row])
        return total

def process_all_units_period(year: int, month: int, 
                             period_kind: Literal["week", "month"] = "week", 
                             week_index: int | None = None) -> dict:
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
        
    for unit in [*config.UNIT_USAHA, "(2)Rincian Transaksi Harian"]:
        try:
            if period_kind == "month":
                res = write_monthly_total(unit, year, month)
            else:
                res = write_period_total(unit, target_period)
            results[unit] = res
        except Exception as e:
            logger.error(f"Failed to process periodic total for unit {unit}: {e}")
            results[unit] = e
            
    return results
