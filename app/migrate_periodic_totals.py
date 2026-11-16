import argparse
import logging
import sys
from datetime import date, datetime

from app.config import config
from app.core.utils import now_tz
from app.services import periodic_total_service

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("migration")

def main():
    parser = argparse.ArgumentParser(description="Migrasi / Backfill Subtotal Mingguan & Total Bulanan")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan ke Google Sheets (default: dry-run)")
    parser.add_argument("--month", type=str, help="Bulan migrasi (YYYY-MM), default: bulan berjalan")
    parser.add_argument("--all-units", action="store_true", default=True, help="Proses semua unit usaha (default: True)")
    
    args = parser.parse_args()
    
    if args.month:
        try:
            target_date = datetime.strptime(args.month, "%Y-%m").date()
        except ValueError:
            logger.error("Format bulan salah. Gunakan YYYY-MM (contoh: 2026-11)")
            sys.exit(1)
    else:
        target_date = now_tz().date()
        
    year, month = target_date.year, target_date.month
    
    logger.info(f"Memulai migrasi untuk periode {year}-{month:02d}")
    if not args.apply:
        logger.info("DRY-RUN: Tidak ada data yang akan ditulis ke spreadsheet.")
    
    boundaries = periodic_total_service.get_period_boundaries(year, month)
    
    for period in boundaries:
        logger.info(f"Memproses {period.label}...")
        
        for unit in config.UNIT_USAHA:
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
                        total = periodic_total_service.write_period_total(unit, period)
                    else:
                        total = periodic_total_service.write_monthly_total(unit, year, month)
                    logger.info(f"  [SUCCESS] {unit}: Pemasukan={total.pemasukan}, Pengeluaran={total.pengeluaran}, Saldo={total.sisa_saldo_akhir}")
                except Exception as e:
                    logger.error(f"  [FAILED] {unit}: {e}")
                    
    logger.info("Migrasi selesai.")

if __name__ == "__main__":
    main()
