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

UNIT_TITLES = {
    "(3)Pertanian ": "Kakorlap Unit Usaha Pertanian",
    "(4)Ternak Ayam Fazza": "Kakorlap Unit Usaha Ternak Ayam Fazza : Agus Adip",
    "(5)Ternak Ayam Emran": "Kakorlap Unit Usaha Ternak Ayam Emran: Agus Adip",
    "(6)Perikanan ": "Kakorlap Unit Usaha Perikanan Emran: Jatmiko",
    "(7)Pabrik Pupuk": "Kakorlap Unit Usaha Pabrik Pupuk ( Widi )",
}

UNIT_HEADERS = {
    "(3)Pertanian ": ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
    "(4)Ternak Ayam Fazza": ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
    "(5)Ternak Ayam Emran": ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
    "(6)Perikanan ": ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
    "(7)Pabrik Pupuk": ["Nomor", "Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
}

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

@dataclass
class TransactionRecord:
    date: date
    nama: str
    keterangan: str
    kuantitas: str
    satuan: str
    tx_id: str
    income: int
    expense: int

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

def _subtotal_label(row: list, description_index: int | None = None) -> str:
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
        "date": find_column("tanggal", "tgl") if normalized else (0 if is_daily else 1),
        "income": find_column("pemasukan (rp)", "pemasukan") if normalized else 6,
        "expense": find_column("pengeluaran (rp)", "pengeluaran", "peneluaran (rp)") if normalized else 7,
        "balance": find_column("sisa saldo", "saldo") if normalized else (None if is_daily else 8),
        "description": find_column("keterangan transaksi", "keterangan") if normalized else (2 if is_daily else 3),
        "tx_id": find_column("transaction id", "transaction_id") if normalized else (5 if is_daily else 9),
        "daily": is_daily,
    }

def _resolved_columns(sheet_name: str, values: list[list]) -> dict[str, int | None]:
    columns = _column_indices(sheet_name, values)
    if columns["income"] is None: columns["income"] = 6
    if columns["expense"] is None: columns["expense"] = 7
    if columns["balance"] is None and not columns["daily"]: columns["balance"] = 8
    if columns["description"] is None: columns["description"] = 2 if columns["daily"] else 3
    if columns["tx_id"] is None: columns["tx_id"] = 5 if columns["daily"] else 9
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

def _build_total_row(sheet_name: str, period: Period, total: PeriodTotal) -> list:
    if sheet_name == "(2)Rincian Transaksi Harian":
        return [
            "", "", period.label, "", "", "",
            total.pemasukan, total.pengeluaran,
        ]
    
    return [
        "",                                           # 0: Nomor
        "",                                           # 1: Tanggal
        "",                                           # 2: Nama
        period.label,                                 # 3: Keterangan
        "",                                           # 4: Kuantitas
        "",                                           # 5: Satuan
        total.pemasukan,                              # 6: Pemasukan
        total.pengeluaran,                            # 7: Pengeluaran
        total.sisa_saldo_akhir,                       # 8: Sisa Saldo
        "",                                           # 9: transaction_id
    ]

def compute_period_total(sheet_name: str, period: Period) -> PeriodTotal:
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return PeriodTotal(period, 0, 0, 0)
    values = worksheet.get_all_values()
    if not values:
        return PeriodTotal(period, 0, 0, 0)
    columns = _resolved_columns(sheet_name, values)
    header_idx = columns["header"] if columns["header"] is not None and columns["header"] >= 0 else 0
    data_rows = values[header_idx + 1:]
    
    date_col = columns["date"] if columns["date"] is not None else (0 if sheet_name == "(2)Rincian Transaksi Harian" else 1)
    matching_rows = []
    for r in data_rows:
        if is_subtotal_row(r): continue
        d = _row_transaction_date(r, date_col)
        if d and period.start_date <= d <= period.end_date:
            matching_rows.append(r)
            
    inc_col = columns["income"] if columns["income"] is not None else 6
    exp_col = columns["expense"] if columns["expense"] is not None else 7
    bal_col = columns["balance"] if columns["balance"] is not None else 8
    
    t_inc = sum(parse_amount(r[inc_col]) for r in matching_rows if len(r) > inc_col)
    t_exp = sum(parse_amount(r[exp_col]) for r in matching_rows if len(r) > exp_col)
    bal = parse_amount(matching_rows[-1][bal_col]) if matching_rows and len(matching_rows[-1]) > bal_col else 0
    return PeriodTotal(period, t_inc, t_exp, bal)

def setup_unit_sheet_layout(sheet_name: str) -> None:
    """Setup Title (baris 1) dan Header (baris 2) untuk 5 unit usaha."""
    if sheet_name not in UNIT_TITLES:
        return
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return
        
    title_text = UNIT_TITLES[sheet_name]
    headers = UNIT_HEADERS[sheet_name]
    
    # Update row 1 (Title) and row 2 (Header)
    sheet_repo._retry_google_call(
        worksheet.update,
        range_name="A1:J2",
        values=[
            [title_text, "", "", "", "", "", "", "", "", ""],
            headers,
        ],
    )
    
    requests = [
        # Merge A1:I1 for title
        {
            "mergeCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9,
                },
                "mergeType": "MERGE_ALL",
            }
        },
        # Title format: 14pt Bold White on Dark Blue
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9,
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 14},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                }},
                "fields": "userEnteredFormat",
            }
        },
        # Header format: Bold White on Dark Blue
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": 0,
                    "endColumnIndex": 10,
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                    "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP",
                }},
                "fields": "userEnteredFormat",
            }
        },
        # Freeze 2 rows (Title + Header)
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "gridProperties": {
                        "frozenRowCount": 2,
                    },
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
    ]
    sheet_repo._retry_google_call(worksheet.spreadsheet.batch_update, {"requests": requests})
    sheet_repo.invalidate_cache(sheet_name)

def _extract_transaction_record(sheet_name: str, row: list) -> TransactionRecord | None:
    is_daily = sheet_name == "(2)Rincian Transaksi Harian"
    if is_daily:
        d = parse_date(row[0] if len(row) > 0 else "")
        if not d: return None
        nama = str(row[1] if len(row) > 1 else "").strip()
        ket = str(row[2] if len(row) > 2 else "").strip()
        qty = str(row[3] if len(row) > 3 else "").strip()
        sat = str(row[4] if len(row) > 4 else "").strip()
        tx_id = str(row[5] if len(row) > 5 else "").strip()
        inc = parse_amount(row[6]) if len(row) > 6 else 0
        exp = parse_amount(row[7]) if len(row) > 7 else 0
        return TransactionRecord(d, nama, ket, qty, sat, tx_id, inc, exp)
    else:
        d = parse_date(row[1] if len(row) > 1 else "") or parse_date(row[0] if len(row) > 0 else "")
        if not d: return None
        nama = str(row[2] if len(row) > 2 else "").strip()
        ket = str(row[3] if len(row) > 3 else "").strip()
        qty = str(row[4] if len(row) > 4 else "").strip()
        sat = str(row[5] if len(row) > 5 else "").strip()
        
        # Cek apakah tx_id ada di kolom 6 (layout lama yang bermasalah)
        if len(row) > 6 and sheet_repo._TRANSACTION_ID.fullmatch(str(row[6]).strip()):
            tx_id = str(row[6]).strip()
            inc = parse_amount(row[7]) if len(row) > 7 else 0
            exp = parse_amount(row[8]) if len(row) > 8 else 0
        else:
            inc = parse_amount(row[6]) if len(row) > 6 else 0
            exp = parse_amount(row[7]) if len(row) > 7 else 0
            tx_id = ""
            for c_idx in [9, 8, 6]:
                if len(row) > c_idx and sheet_repo._TRANSACTION_ID.fullmatch(str(row[c_idx]).strip()):
                    tx_id = str(row[c_idx]).strip()
                    break
        return TransactionRecord(d, nama, ket, qty, sat, tx_id, inc, exp)

def rebuild_sheet_with_totals(sheet_name: str) -> None:
    with _PERIOD_LOCK:
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if not worksheet:
            logger.warning(f"Worksheet tidak ditemukan: {sheet_name}")
            return
            
        values = worksheet.get_all_values()
        if not values:
            return
            
        is_daily = sheet_name == "(2)Rincian Transaksi Harian"
        
        if not is_daily and sheet_name in UNIT_TITLES:
            setup_unit_sheet_layout(sheet_name)
            header_idx = 1
            start_row = 3
        else:
            header_idx = 4 if is_daily else sheet_repo._detect_header_row_index(sheet_name, values)
            start_row = header_idx + 2
            
        data_rows = values[header_idx + 1:] if len(values) > header_idx + 1 else []
        
        txs = []
        for row in data_rows:
            if not any(str(cell).strip() for cell in row):
                continue
            if is_subtotal_row(row):
                continue
            rec = _extract_transaction_record(sheet_name, row)
            if rec:
                txs.append(rec)
                
        # Urutkan secara kronologis berdasarkan tanggal dan TxID
        txs.sort(key=lambda t: (t.date, t.tx_id))
        
        months_data = defaultdict(list)
        for t in txs:
            months_data[f"{t.date.year}-{t.date.month:02d}"].append(t)
            
        final_rows = []
        total_row_indices = []
        running_balance = 0
        tx_counter = 1
        
        for month_key, month_txs in sorted(months_data.items()):
            year, month = map(int, month_key.split("-"))
            boundaries = get_period_boundaries(year, month)
            
            for p in boundaries:
                if p.kind != "week":
                    continue
                week_txs = [t for t in month_txs if p.start_date <= t.date <= p.end_date]
                if not week_txs:
                    continue
                    
                w_income = 0
                w_expense = 0
                
                for t in week_txs:
                    inc = t.income
                    exp = t.expense
                    running_balance = running_balance + inc - exp  # Pengurangan yang jelas
                    w_income += inc
                    w_expense += exp
                    
                    if is_daily:
                        # 8 Kolom: Tanggal, Nama, Keterangan, Kuantitas, Satuan, TxID, Pemasukan, Pengeluaran
                        row = [
                            format_tanggal_id(t.date),
                            t.nama,
                            t.keterangan,
                            t.kuantitas,
                            t.satuan,
                            t.tx_id,
                            inc if inc > 0 else "",
                            exp if exp > 0 else "",
                        ]
                    else:
                        # 10 Kolom: Nomor, Tanggal, Nama, Keterangan, Kuantitas, Satuan, Pemasukan, Pengeluaran, Saldo, TxID
                        row = [
                            str(tx_counter),
                            format_tanggal_id(t.date),
                            t.nama,
                            t.keterangan,
                            t.kuantitas,
                            t.satuan,
                            inc if inc > 0 else "",
                            exp if exp > 0 else "",
                            running_balance,
                            t.tx_id,
                        ]
                        tx_counter += 1
                    final_rows.append(row)
                    
                # Baris Subtotal Minggu
                if is_daily:
                    subtotal_row = [
                        "", "", p.label, "", "", "",
                        w_income, w_expense,
                    ]
                else:
                    subtotal_row = [
                        "", "", "", p.label, "", "",
                        w_income, w_expense, running_balance, "",
                    ]
                final_rows.append(subtotal_row)
                total_row_indices.append(len(final_rows))
                
            # Baris Total Bulan
            month_p = boundaries[-1]
            m_income = sum(t.income for t in month_txs)
            m_expense = sum(t.expense for t in month_txs)
            if is_daily:
                month_row = [
                    "", "", month_p.label, "", "", "",
                    m_income, m_expense,
                ]
            else:
                month_row = [
                    "", "", "", month_p.label, "", "",
                    m_income, m_expense, running_balance, "",
                ]
            final_rows.append(month_row)
            total_row_indices.append(len(final_rows))
            
        end_row = start_row + len(final_rows) - 1
        
        if len(final_rows) > 0:
            sheet_repo.rewrite_period_block(sheet_name, start_row, end_row, final_rows)
            
            for rel_idx in total_row_indices:
                abs_row_index = start_row + rel_idx - 1
                try:
                    sheet_repo.format_total_row(sheet_name, abs_row_index)
                except Exception as e:
                    logger.warning(f"Gagal format baris total {sheet_name} row {abs_row_index}: {e}")
                    
            # Hapus sisa baris lama jika data baru lebih pendek
            old_rows_count = len(values)
            if old_rows_count > end_row:
                to_delete = list(range(end_row + 1, old_rows_count + 1))
                if to_delete:
                    sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)
        elif len(final_rows) == 0 and len(data_rows) > 0:
            to_delete = list(range(start_row, start_row + len(data_rows)))
            sheet_repo.delete_rows_bottom_up(sheet_name, to_delete)
        
        logger.info(f"Rebuild sheet {sheet_name} selesai ({len(final_rows)} rows)")

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

def normalize_row_formatting(sheet_name: str) -> int:
    return 0

def normalize_sheet_rows(sheet_name: str) -> int:
    return 0

def ensure_sorted(sheet_name: str, period: Period) -> bool:

    return True

def write_period_total(sheet_name: str, period: Period, force: bool = False) -> PeriodTotal:
    return compute_period_total(sheet_name, period)

def write_monthly_total(sheet_name: str, year: int, month: int, force: bool = False) -> PeriodTotal:
    boundaries = get_period_boundaries(year, month)
    monthly_period = next(p for p in boundaries if p.kind == "month")
    return compute_period_total(sheet_name, monthly_period)

def process_all_units_period(year: int, month: int,
                             period_kind: Literal["week", "month"] = "week",
                             week_index: int | None = None,
                             force: bool = False,
                             unit_names: list[str] | None = None) -> dict:
    return rebuild_all_sheets_with_totals()
