import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from app.config import config
from app.core.utils import now_tz
from app.repositories import sheet_repo
from app.services import periodic_total_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("migration")
DAILY_SHEET = "(2)Rincian Transaksi Harian"
ALL_SHEETS = [*config.LEDGER_UNITS, DAILY_SHEET]
BACKUP_DIR = Path(__file__).resolve().parents[1] / "data_backups"


def _backup_sheets(sheet_names: list[str], year: int, month: int) -> Path:
    values_by_sheet = {}
    for sheet_name in sheet_names:
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if not worksheet:
            raise RuntimeError(f"Worksheet tidak ditemukan: {sheet_name}")
        values_by_sheet[sheet_name] = sheet_repo._retry_google_call(worksheet.get_all_values)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = BACKUP_DIR / f"periodic_totals_{year}_{month:02d}_{timestamp}.json"
    backup_path.write_text(json.dumps(values_by_sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    backup_path.chmod(0o600)
    return backup_path

def main():
    parser = argparse.ArgumentParser(description="Backfill subtotal mingguan dan total bulanan untuk ledger dan rincian harian.")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan ke Google Sheets (default: dry-run)")
    parser.add_argument("--month", type=str, help="Bulan migrasi (YYYY-MM), default: bulan berjalan")
    parser.add_argument("--sheet", choices=ALL_SHEETS, help="Batasi ke satu worksheet; default lima ledger unit, rincian harian opt-in")
    parser.add_argument("--all-units", action="store_true", default=True, help="Proses semua lima ledger unit (default)")

    args = parser.parse_args()
    today = now_tz().date()
    
    if args.month:
        try:
            target_date = datetime.strptime(args.month, "%Y-%m").date()
        except ValueError:
            logger.error("Format bulan salah. Gunakan YYYY-MM (contoh: 2026-11)")
            sys.exit(1)
    else:
        target_date = today
        
    year, month = target_date.year, target_date.month
    sheet_names = [args.sheet] if args.sheet else config.LEDGER_UNITS
    
    logger.info(f"Memulai migrasi untuk periode {year}-{month:02d}")
    if not args.apply:
        logger.info("DRY-RUN: hanya menghitung total; tidak ada baris total yang ditulis.")
    
    boundaries = periodic_total_service.get_period_boundaries(year, month)

    if args.apply:
        try:
            backup_path = _backup_sheets(sheet_names, year, month)
            logger.info("Snapshot nilai sheet tersimpan: %s", backup_path)
        except Exception as error:
            logger.error("Backfill dibatalkan sebelum menulis karena snapshot gagal: %s", error)
            return 1
    
    failures = 0
    for period in boundaries:
        logger.info(f"Memproses {period.label}...")
        if today < period.end_date:
            for unit in sheet_names:
                logger.info(
                    "  [SKIP] %s: boundary %s belum tercapai; total tidak ditulis.",
                    unit, period.end_date,
                )
            continue
        
        for unit in sheet_names:
            if not args.apply:
                # Dry run: calculate but don't write
                try:
                    total = periodic_total_service.compute_period_total(unit, period)
                    logger.info(f"  [DRY-RUN] {unit}: Pemasukan={total.pemasukan}, Pengeluaran={total.pengeluaran}, Saldo={total.sisa_saldo_akhir}")
                except Exception as e:
                    logger.error(f"  [DRY-RUN] {unit} FAILED: {e}")
            else:
                # Apply
                try:
                    if period.kind == "week":
                        total = periodic_total_service.write_period_total(unit, period, force=True)
                    else:
                        total = periodic_total_service.write_monthly_total(unit, year, month, force=True)
                    logger.info(f"  [SUCCESS] {unit}: Pemasukan={total.pemasukan}, Pengeluaran={total.pengeluaran}, Saldo={total.sisa_saldo_akhir}")
                except Exception as e:
                    failures += 1
                    logger.error(f"  [FAILED] {unit}: {e}")
                    
    logger.info("Backfill selesai dengan %d kegagalan.", failures)
    return 1 if failures else 0

if __name__ == "__main__":
    main()
