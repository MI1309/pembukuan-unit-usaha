import calendar
import logging
from datetime import date, datetime, timedelta

from app.config import config
from app.core.utils import now_tz, parse_date
from app.repositories import sheet_repo
from app.services import periodic_total_service

logger = logging.getLogger(__name__)


def get_target_archive_month(year: int | None = None, month: int | None = None) -> tuple[int, int]:
    """Menentukan bulan dan tahun yang akan diarsipkan.
    Jika tidak disebutkan, otomatis mengambil bulan sebelumnya dari hari ini."""
    if year and month:
        return year, month

    today = now_tz().date()
    first_of_this_month = date(today.year, today.month, 1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    return last_day_prev_month.year, last_day_prev_month.month


def _extract_unit_rows_for_month(rows: list[list], target_year: int, target_month: int) -> list[tuple[int, list]]:
    archived = []
    for row_number, row in enumerate(rows[2:], start=3):
        if not any(str(c).strip() for c in row):
            continue
        # Cek kolom 1 (Tanggal) atau kolom 0 (Nomor/Tanggal)
        row_date = parse_date(row[1] if len(row) > 1 else "") or parse_date(row[0] if len(row) > 0 else "")
        if row_date and (row_date.year, row_date.month) == (target_year, target_month):
            archived.append((row_number, row))
    return archived


def archive_month_ledger(target_year: int, target_month: int) -> dict:
    """Mengarsipkan seluruh transaksi bulan target dari 5 unit usaha + Rincian Harian."""
    logger.info("Memulai pengarsipan bulanan untuk periode %04d-%02d", target_year, target_month)
    archived_at = now_tz().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Arsipkan 5 unit usaha
    rows_by_unit = {}
    for unit in config.UNIT_USAHA:
        sheet = sheet_repo.get_sheet(unit, create=False)
        if not sheet:
            continue
        rows = _extract_unit_rows_for_month(sheet.get_all_values(), target_year, target_month)
        if rows:
            rows_by_unit[unit] = rows

    unit_results = {}
    if rows_by_unit:
        unit_results = sheet_repo.archive_unit_transactions(rows_by_unit, archived_at)
        logger.info("Arsip unit usaha selesai: %s", unit_results)

    # 2. Arsipkan Rincian Harian jika ada data bulan target
    daily_sheet = sheet_repo.get_sheet("(2)Rincian Transaksi Harian", create=False)
    daily_count = 0
    if daily_sheet:
        daily_values = daily_sheet.get_all_values()
        daily_rows = []
        for r in daily_values[5:]:
            r_date = parse_date(r[0] if len(r) > 0 else "")
            if r_date and (r_date.year, r_date.month) == (target_year, target_month):
                daily_rows.append(r[:7])

        if daily_rows:
            month_key = f"{target_year:04d}-{target_month:02d}"
            next_first = (date(target_year, target_month, 1) + timedelta(days=32)).replace(day=1)
            next_last = calendar.monthrange(next_first.year, next_first.month)[1]
            next_label = f"Periode: 01 {next_first:%b %Y} s/d {next_last:02d} {next_first:%b %Y} Semua Unit Usaha"
            sheet_repo.archive_daily_detail(month_key, daily_rows, next_label)
            daily_count = len(daily_rows)

    # 3. Rebuild seluruh sheet agar layout dan subtotal bulan baru bersih
    rebuild_res = periodic_total_service.rebuild_all_sheets_with_totals()

    return {
        "status": "success",
        "target_period": f"{target_year:04d}-{target_month:02d}",
        "archived_at": archived_at,
        "units_archived": {u: len(r) for u, r in rows_by_unit.items()},
        "daily_detail_archived": daily_count,
        "unit_results": unit_results,
        "rebuild_results": rebuild_res,
    }
