import calendar
import logging
import re
from collections import defaultdict
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
    _, last_day = calendar.monthrange(year, month)
    
    def get_month_name(y, m):
        dt = datetime(y, m, 1)
        return format_tanggal_id(dt).split()[-2]

    month_label = get_month_name(year, month)
    year_str = str(year)

    periods = []
    week_configs = [(1, 7, 1), (8, 14, 2), (15, 21, 3), (22, last_day, 4)]
    
    for start, end, idx in week_configs:
        label = f"SUBTOTAL MINGGU {idx} ({start:02d}-{end:02d} {month_label} {year_str})"
        periods.append(Period(
            label=label,
            start_date=date(year, month, start),
            end_date=date(year, month, end),
            kind="week"
        ))
        
    monthly_label = f"TOTAL BULAN {month_label} {year_str}"
    periods.append(Period(
        label=monthly_label,
        start_date=date(year, month, 1),
        end_date=date(year, month, last_day),
        kind="month"
    ))
    
    return periods

def get_period_for_date(target: date) -> Period:
    boundaries = get_period_boundaries(target.year, target.month)
    for p in boundaries:
        if p.kind == "week" and p.start_date <= target <= p.end_date:
            return p
    return boundaries[3]

def is_subtotal_row(row: list) -> bool:
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
        "tx_id": find_column("transaction id", "transaction_id"),
        "daily": is_daily,
    }

def _resolved_columns(sheet_name: str, values: list[list]) -> dict[str, int | None]:
    columns = _column_indices(sheet_name, values)
    if columns["income"] is None: columns["income"] = 6 if columns["daily"] else 7
    if columns["expense"] is None: columns["expense"] = 7 if columns["daily"] else 8
    if columns["balance"] is None and not columns["daily"]: columns["balance"] = 9
    if columns["description"] is None: columns["description"] = 2 if columns["daily"] else 3
    if columns["tx_id"] is None: columns["tx_id"] = 5 if columns["daily"] else 6
    return columns

def _parse_ledger_date(value):
    parsed = parse_date(value)
    if parsed: return parsed
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    return None

def _row_transaction_date(row: list, date_index: int) -> date | None:
    parsed = _parse_ledger_date(row[date_index] if len(row) > date_index else "")
    if parsed: return parsed
    for index in range(date_index + 1, len(row)):
        if any(str(value).strip() for value in row[date_index:index]):
            break
        parsed = _parse_ledger_date(row[index])
        if parsed: return parsed
    return None

def compute_period_total(sheet_name: str, period: Period) -> PeriodTotal:
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet: return PeriodTotal(period, 0, 0, 0)
    
    values = worksheet.get_all_values()
    if not values: return PeriodTotal(period, 0, 0, 0)
    
    columns = _resolved_columns(sheet_name, values)
    total_pemasukan = 0
    total_pengeluaran = 0
    last_saldo = 0
    found_in_period = False
    last_saldo_before = 0
    last_before_date = None
    last_period_date = None
    
    for row_index, row in enumerate(values):
        if row_index <= columns["header"] or is_subtotal_row(row): continue

        date_index = columns["date"] or 0
        row_date = _row_transaction_date(row, date_index)
        if not row_date: continue

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

def _build_total_row(sheet_name: str, period: Period, total: PeriodTotal) -> list:
    if sheet_name == "(2)Rincian Transaksi Harian":
        # Rincian Harian: Tanggal, Nama, Ket, Qty, Satuan, transaction_id, Pemasukan, Pengeluaran
        return [
            "", "", period.label, "", "", "",
            total.pemasukan, total.pengeluaran,
        ]
    
    # 5 Unit Usaha: Nomor, Tgl, Nama, Ket, Qty, Satuan, transaction_id, Pemasukan, Pengeluaran, Saldo
    return [
        "",                                           # 0: Nomor
        period.end_date.strftime("%Y-%m-%d"),          # 1: Tanggal
        "",                                           # 2: Nama
        period.label,                                 # 3: Keterangan
        "",                                           # 4: Kuantitas
        "",                                           # 5: Satuan
        "",                                           # 6: transaction_id
        total.pemasukan,                              # 7: Pemasukan
        total.pengeluaran,                            # 8: Pengeluaran
        total.sisa_saldo_akhir,                       # 9: Sisa Saldo
    ]

def rebuild_sheet_with_totals(sheet_name: str) -> None:
    with _PERIOD_LOCK:
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if not worksheet: return
            
        values = worksheet.get_all_values()
        if not values: return
            
        columns = _resolved_columns(sheet_name, values)
        header_idx = columns["header"]
        if header_idx < 0: return
            
        data_rows = values[header_idx + 1:]
        
        clean_rows = []
        for row in data_rows:
            if not any(str(cell).strip() for cell in row): continue
            if is_subtotal_row(row): continue
            clean_rows.append(row)
            
        date_idx = columns["date"] if columns["date"] is not None else 1
        tx_idx = columns["tx_id"]
        clean_rows.sort(key=lambda r: (
            _row_transaction_date(r, date_idx) or date(9999, 12, 31),
            str(r[tx_idx]).strip() if tx_idx is not None and tx_idx < len(r) else ""
        ))
        
        months_data = defaultdict(list)
        for row in clean_rows:
            d = _row_transaction_date(row, date_idx)
            if d: months_data[f"{d.year}-{d.month:02d}"].append(row)
                
        final_rows = []
        total_row_indices = []
        
        for month_key, month_rows in sorted(months_data.items()):
            year, month = map(int, month_key.split("-"))
            boundaries = get_period_boundaries(year, month)
            
            for p in boundaries:
                if p.kind != "week": continue
                week_rows = [r for r in month_rows if p.start_date <= _row_transaction_date(r, date_idx) <= p.end_date]
                if week_rows:
                    final_rows.extend(week_rows)
                    
                    t_pemasukan = sum(parse_amount(r[columns["income"]]) for r in week_rows if columns["income"] is not None and len(r) > columns["income"] and r[columns["income"]] != "")
                    t_pengeluaran = sum(parse_amount(r[columns["expense"]]) for r in week_rows if columns["expense"] is not None and len(r) > columns["expense"] and r[columns["expense"]] != "")
                    saldo = parse_amount(week_rows[-1][columns["balance"]]) if columns["balance"] is not None and len(week_rows[-1]) > columns["balance"] else 0
                    
                    p_total = PeriodTotal(p, t_pemasukan, t_pengeluaran, saldo)
                    final_rows.append(_build_total_row(sheet_name, p, p_total))
                    total_row_indices.append(len(final_rows))
                    
            month_p = boundaries[-1]
            month_dates = [_row_transaction_date(r, date_idx) for r in month_rows if _row_transaction_date(r, date_idx)]
            last_tx_date = max(month_dates) if month_dates else None
            
            if (last_tx_date and last_tx_date >= month_p.end_date) or (now_tz().date() >= month_p.end_date):
                t_pemasukan = sum(parse_amount(r[columns["income"]]) for r in month_rows if columns["income"] is not None and len(r) > columns["income"] and r[columns["income"]] != "")
                t_pengeluaran = sum(parse_amount(r[columns["expense"]]) for r in month_rows if columns["expense"] is not None and len(r) > columns["expense"] and r[columns["expense"]] != "")
                saldo = parse_amount(month_rows[-1][columns["balance"]]) if columns["balance"] is not None and len(month_rows[-1]) > columns["balance"] else 0
                
                m_total = PeriodTotal(month_p, t_pemasukan, t_pengeluaran, saldo)
                final_rows.append(_build_total_row(sheet_name, month_p, m_total))
                total_row_indices.append(len(final_rows))
                
        start_row = header_idx + 2
        end_row = start_row + len(final_rows) - 1
        
        if len(final_rows) > 0:
            sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, final_rows)
            
            for rel_idx in total_row_indices:
                abs_row_index = start_row + rel_idx - 1
                try:
                    sheet_repo.format_total_row(sheet_name, abs_row_index)
                except Exception as e:
                    logger.warning(f"Gagal format baris total {sheet_name} row {abs_row_index}: {e}")
            
            if len(final_rows) < len(data_rows):
                to_delete = list(range(end_row + 1, start_row + len(data_rows)))
                if to_delete:
                    sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)
        elif len(final_rows) == 0 and len(data_rows) > 0:
            to_delete = list(range(start_row, start_row + len(data_rows)))
            sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)

# ==============================================================================
# FUNGSI YANG KETINGGALAN (INI PENYEBAB ERRORNYA)
# ==============================================================================
def rebuild_all_sheets_with_totals() -> dict:
    """Merombak dan menghitung ulang 5 unit usaha + Rincian Harian sekaligus."""
    units = list(config.UNIT_USAHA) + ["(2)Rincian Transaksi Harian"]
    results = {}
    
    for unit in units:
        try:
            rebuild_sheet_with_totals(unit)
            results[unit] = "Sukses"
        except Exception as e:
            logger.error(f"Gagal rebuild {unit}: {e}")
            results[unit] = str(e)
            
    return results