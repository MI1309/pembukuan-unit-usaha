import calendar
import re
from collections import defaultdict
from datetime import date, datetime
from threading import RLock

from app.config import config
from app.core.utils import format_rupiah, parse_amount
from app.repositories import sheet_repo

REPORT_SHEET = "_RekapBulanan"
REPORT_SHEET_ALIASES = (REPORT_SHEET, "(1)Rekap Bulanan ")
SALES_DETAIL_SHEET = "Rincian Penjualan"
REPORT_HEADERS = [
    "Bulan", "Produk/Jasa/Barang", "Kuantitas", "Satuan", "Total (Rp)",
    "Jumlah Transaksi",
]
_REPORT_LOCK = RLock()
_MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
    "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
)


def _parse_period_month(target_month: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-\d{2}", str(target_month or "")):
        raise ValueError("Bulan harus berformat YYYY-MM, contoh: 2026-11.")
    year, month = map(int, target_month.split("-"))
    if year < 1 or not 1 <= month <= 12:
        raise ValueError("Bulan tidak valid. Gunakan format YYYY-MM.")
    return year, month


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_quantity(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return float(parse_amount(text))


def _is_in_period(value, start: date, end: date) -> bool:
    row_date = _parse_date(value)
    return row_date is not None and start <= row_date <= end


def _unit_sheet_rows(unit_usa: str, start: date, end: date) -> list[list]:
    worksheet = sheet_repo.get_sheet(unit_usa, create=False)
    if worksheet is None:
        raise ValueError(f"Worksheet unit usaha tidak ditemukan: {unit_usa}")
    values = worksheet.get_all_values()
    return [
        row for row in values[2:]
        if len(row) > 7 and _is_in_period(row[1], start, end)
    ]


def generate_laporan_periode(unit_usa: str, start_date: str, end_date: str) -> dict:
    """Hitung pemasukan, pengeluaran, dan laba/rugi satu unit dalam rentang tanggal."""
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None or start > end:
        raise ValueError("Tanggal awal/akhir tidak valid atau rentangnya terbalik.")

    rows = _unit_sheet_rows(unit_usa, start, end)
    income = sum(parse_amount(row[6]) for row in rows)
    expense = sum(parse_amount(row[7]) for row in rows)
    return {
        "unit_usa": unit_usa,
        "total_pemasukan": income,
        "total_pengeluaran": expense,
        "laba_rugi": income - expense,
    }


def _unit_recap_rows(unit_usa: str, start: date, end: date, period_name: str) -> list[list]:
    rows = _unit_sheet_rows(unit_usa, start, end)
    recap_rows = []
    for label, amount_column in (("Pemasukan", 6), ("Pengeluaran", 7)):
        qualifying_rows = [row for row in rows if parse_amount(row[amount_column]) > 0]
        grouped = defaultdict(lambda: {"quantity": 0.0, "amount": 0, "count": 0})
        for row in qualifying_rows:
            unit = str(row[5]).strip() or "-"
            grouped[unit]["quantity"] += _parse_quantity(row[4])
            grouped[unit]["amount"] += parse_amount(row[amount_column])
            grouped[unit]["count"] += 1
        if not grouped:
            grouped["-"]
        for unit, totals in sorted(grouped.items()):
            recap_rows.append([
                period_name,
                f"{unit_usa.strip()} - {label}",
                totals["quantity"],
                unit,
                totals["amount"],
                totals["count"],
            ])
    return recap_rows


def _sales_recap_rows(start: date, end: date, period_name: str) -> list[list]:
    worksheet = sheet_repo.get_sheet(SALES_DETAIL_SHEET, create=False)
    if worksheet is None:
        raise ValueError(f"Worksheet tidak ditemukan: {SALES_DETAIL_SHEET}")

    grouped = defaultdict(lambda: {"quantity": 0.0, "amount": 0, "count": 0})
    for row in worksheet.get_all_values()[1:]:
        if len(row) < 7 or not _is_in_period(row[2], start, end):
            continue
        amount = parse_amount(row[6])
        if amount <= 0:
            continue
        product = str(row[0]).strip() or "Tanpa nama"
        unit = str(row[5]).strip() or "-"
        key = (product, unit)
        grouped[key]["quantity"] += _parse_quantity(row[4])
        grouped[key]["amount"] += amount
        grouped[key]["count"] += 1

    return [
        [period_name, f"Rincian Penjualan - {product}", totals["quantity"], unit,
         totals["amount"], totals["count"]]
        for (product, unit), totals in sorted(grouped.items())
    ]


def _report_worksheet():
    for name in REPORT_SHEET_ALIASES:
        worksheet = sheet_repo.get_sheet(name, create=False)
        if worksheet is not None:
            return worksheet
    raise ValueError("Worksheet _RekapBulanan tidak ditemukan; laporan tidak membuat sheet baru.")


def simpan_laporan_ke_sheet(periode_name: str, start_date: str, end_date: str) -> dict:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start is None or end is None or start > end:
        raise ValueError("Tanggal awal/akhir tidak valid atau rentangnya terbalik.")

    rows_to_write = []
    unit_totals = []
    for unit_usa in config.UNIT_USAHA:
        unit_totals.append(generate_laporan_periode(unit_usa, start_date, end_date))
        rows_to_write.extend(_unit_recap_rows(unit_usa, start, end, periode_name))
    rows_to_write.extend(_sales_recap_rows(start, end, periode_name))

    with _REPORT_LOCK:
        worksheet = _report_worksheet()
        existing_rows = worksheet.get_all_values()
        duplicate_row_numbers = [
            row_number for row_number, row in enumerate(existing_rows[1:], start=2)
            if row and str(row[0]).strip() == periode_name
        ]
        for row_number in reversed(duplicate_row_numbers):
            worksheet.delete_rows(row_number)
        if rows_to_write:
            worksheet.append_rows(rows_to_write, value_input_option="USER_ENTERED")
        sheet_repo.invalidate_cache(worksheet.title)

    return {
        "periode": periode_name,
        "total_pemasukan_unit": sum(item["total_pemasukan"] for item in unit_totals),
        "total_pengeluaran_unit": sum(item["total_pengeluaran"] for item in unit_totals),
        "laba_rugi_unit": sum(item["laba_rugi"] for item in unit_totals),
        "total_penjualan_rincian": sum(row[4] for row in rows_to_write if row[1].startswith("Rincian Penjualan - ")),
        "jumlah_baris": len(rows_to_write),
    }


def _period_label(week_number: int, year: int, month: int) -> str:
    return f"Minggu {week_number} - {_MONTH_ABBREVIATIONS[month - 1]} {year}"


def jalankan_rekap_mingguan(target_month: str) -> str:
    year, month = _parse_period_month(target_month)
    last_day = calendar.monthrange(year, month)[1]
    periods = []
    for week_number in range(1, 5):
        start_day = (week_number - 1) * 7 + 1
        end_day = last_day if week_number == 4 else week_number * 7
        summary = simpan_laporan_ke_sheet(
            _period_label(week_number, year, month),
            date(year, month, start_day).isoformat(),
            date(year, month, end_day).isoformat(),
        )
        periods.append(summary)

    lines = [f"Rekap mingguan { _MONTH_ABBREVIATIONS[month - 1] } {year} berhasil disimpan."]
    for summary in periods:
        lines.extend([
            f"{summary['periode']}: pemasukan unit {format_rupiah(summary['total_pemasukan_unit'])}, "
            f"pengeluaran unit {format_rupiah(summary['total_pengeluaran_unit'])}, "
            f"laba/rugi {format_rupiah(summary['laba_rugi_unit'])}, "
            f"penjualan rincian {format_rupiah(summary['total_penjualan_rincian'])}.",
        ])
    return "\n".join(lines)


def jalankan_rekap_bulanan(target_month: str) -> str:
    year, month = _parse_period_month(target_month)
    month_name = config.BULAN[month - 1]
    period_name = f"{month_name} {year}"
    summary = simpan_laporan_ke_sheet(
        period_name,
        date(year, month, 1).isoformat(),
        date(year, month, calendar.monthrange(year, month)[1]).isoformat(),
    )
    return (
        f"Rekap bulanan {period_name} berhasil disimpan.\n"
        f"Pemasukan unit: {format_rupiah(summary['total_pemasukan_unit'])}\n"
        f"Pengeluaran unit: {format_rupiah(summary['total_pengeluaran_unit'])}\n"
        f"Laba/rugi unit: {format_rupiah(summary['laba_rugi_unit'])}\n"
        f"Penjualan rincian: {format_rupiah(summary['total_penjualan_rincian'])}"
    )