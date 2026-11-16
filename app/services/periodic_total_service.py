import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime
from threading import RLock
from typing import Literal

from app.config import config
from app.core.utils import format_tanggal_id, now_tz, parse_amount
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
    """True jika kolom Keterangan (index 2) berawalan
    '[SUBTOTAL MINGGU' atau '[TOTAL BULAN'."""
    if len(row) > 2:
        keterangan = str(row[2]).strip().upper()
        return keterangan.startswith("SUBTOTAL MINGGU") or keterangan.startswith("TOTAL BULAN")
    return False

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
    
    # Ledger unit usaha (0-based): 
    # 0: Tanggal, 1: Produk, 2: Keterangan, 3: Qty, 4: Satuan, 5: Pemasukan, 6: Pengeluaran, 7: Saldo, 8: TxID
    
    total_pemasukan = 0
    total_pengeluaran = 0
    last_saldo = 0
    found_in_period = False
    
    # Find last saldo before period start
    last_saldo_before = 0
    
    for row in values:
        if is_subtotal_row(row):
            continue
            
        row_date = sheet_repo._parse_sheet_date(row[0] if len(row) > 0 else "")
        if not row_date:
            continue
            
        row_pemasukan = parse_amount(row[5]) if len(row) > 5 else 0
        row_pengeluaran = parse_amount(row[6]) if len(row) > 6 else 0
        row_saldo = parse_amount(row[7]) if len(row) > 7 else 0
        
        if row_date < period.start_date:
            last_saldo_before = row_saldo
        elif period.start_date <= row_date <= period.end_date:
            total_pemasukan += row_pemasukan
            total_pengeluaran += row_pengeluaran
            last_saldo = row_saldo
            found_in_period = True
            
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
        
    block_indices = [] # 1-based indices
    block_rows = []
    
    for idx, row in enumerate(values):
        if is_subtotal_row(row):
            continue
            
        row_date = sheet_repo._parse_sheet_date(row[0] if len(row) > 0 else "")
        if row_date and period.start_date <= row_date <= period.end_date:
            block_indices.append(idx + 1)
            block_rows.append(row)
            
    if not block_rows:
        return False
        
    # Find TxID column
    header_row_idx = sheet_repo._detect_header_row_index(sheet_name, values)
    header = values[header_row_idx] if header_row_idx < len(values) else []
    tx_id_idx = next((i for i, h in enumerate(header) if str(h).strip().upper() == "TRANSACTION ID"), None)
    
    def sort_key(row):
        d = sheet_repo._parse_sheet_date(row[0] if len(row) > 0 else "")
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
        
        # Replace existing subtotal
        # The label starts with "SUBTOTAL MINGGU X" or "TOTAL BULAN"
        # We use a prefix to find them.
        label_prefix = period.label.split('(')[0].strip()
        old_rows = sheet_repo.find_subtotal_rows(sheet_name, label_prefix)
        # Filter only for the exact period if needed, but the prompt says replace by prefix
        # To be safe, let's check if the label matches more closely
        to_delete = []
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if worksheet:
            values = worksheet.get_all_values()
            for idx in old_rows:
                if idx <= len(values):
                    row = values[idx-1]
                    if len(row) > 2 and period.label in str(row[2]):
                        to_delete.append(idx)
        
        if to_delete:
            sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)
            
        total = compute_period_total(sheet_name, period)
        
        # Determine insert position
        values = worksheet.get_all_values() if worksheet else []
        last_row_idx = 0
        
        # Find header to know where data starts
        header_row_idx = sheet_repo._detect_header_row_index(sheet_name, values)
        last_row_idx = header_row_idx + 1 # Start after header
        
        for idx, row in enumerate(values):
            if is_subtotal_row(row):
                continue
            row_date = sheet_repo._parse_sheet_date(row[0] if len(row) > 0 else "")
            if row_date:
                if row_date <= period.end_date:
                    last_row_idx = idx + 1
        
        # Build row
        # [Nomor="", Tanggal=period.end_date, Nama Barang="", Keterangan=period.label, Kuantitas="", Satuan="", Pemasukan, Pengeluaran, Saldo]
        new_row = [
            "", period.end_date.strftime("%Y-%m-%d"), "", period.label,
            "", "", total.pemasukan, total.pengeluaran, total.sisa_saldo_akhir
        ]
        
        sheet_repo.insert_rows_after(sheet_name, last_row_idx, [new_row])
        return total

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
        
        # Remove old monthly total
        label_prefix = f"TOTAL BULAN"
        old_rows = sheet_repo.find_subtotal_rows(sheet_name, label_prefix)
        to_delete = []
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if worksheet:
            values = worksheet.get_all_values()
            for idx in old_rows:
                if idx <= len(values):
                    row = values[idx-1]
                    if len(row) > 2 and monthly_period.label in str(row[2]):
                        to_delete.append(idx)
        if to_delete:
            sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)
            
        total = compute_period_total(sheet_name, monthly_period)
        
        # Find insert position: after Week 4 subtotal or after last transaction
        values = worksheet.get_all_values() if worksheet else []
        insert_after = 0
        
        # Try to find Week 4 subtotal
        week4_label = week4_period.label
        for idx, row in enumerate(values):
            if len(row) > 2 and week4_label in str(row[2]):
                insert_after = idx + 1
                break
        
        if insert_after == 0:
            # Fallback to last transaction in month
            header_row_idx = sheet_repo._detect_header_row_index(sheet_name, values)
            insert_after = header_row_idx + 1
            for idx, row in enumerate(values):
                if is_subtotal_row(row):
                    continue
                row_date = sheet_repo._parse_sheet_date(row[0] if len(row) > 0 else "")
                if row_date and row_date <= monthly_period.end_date:
                    insert_after = idx + 1
                    
        new_row = [
            "", monthly_period.end_date.strftime("%Y-%m-%d"), "", monthly_period.label,
            "", "", total.pemasukan, total.pengeluaran, total.sisa_saldo_akhir
        ]
        
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
        
    for unit in config.UNIT_USAHA:
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
