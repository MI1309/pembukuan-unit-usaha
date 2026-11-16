import argparse
import logging
from datetime import datetime

from app.config import config
from app.repositories import sheet_repo
from app.services import periodic_total_service as totals

logger = logging.getLogger(__name__)
DAILY_DETAIL_SHEET = "(2)Rincian Transaksi Harian"


def _monthly_total_rows(sheet_name: str, values: list[list], year: int, month: int) -> list[int]:
    month_year = f"{config.BULAN[month - 1]} {year}".casefold()
    columns = totals._resolved_columns(sheet_name, values)
    description_index = columns["description"] if columns["description"] is not None else 2
    rows = []
    for index, row in enumerate(values):
        if index <= columns["header"] or description_index >= len(row):
            continue
        label = str(row[description_index]).strip().lstrip("[").casefold()
        if month_year in label and label.startswith(("subtotal", "total bulan")):
            rows.append(index + 1)
    return rows


def _process_sheet(sheet_name: str, year: int, month: int, apply: bool) -> dict:
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return {"status": "skip", "reason": "worksheet not found"}

    values = worksheet.get_all_values()
    old_total_rows = _monthly_total_rows(sheet_name, values, year, month)
    periods = totals.get_period_boundaries(year, month)
    weekly_periods = [period for period in periods if period.kind == "week"]
    if not apply:
        return {
            "status": "planned",
            "removed_rows": len(old_total_rows),
            "totals": [totals.compute_period_total(sheet_name, period) for period in periods],
        }

    if old_total_rows:
        sheet_repo.delete_rows_bottom_up(sheet_name, old_total_rows)
    totals.normalize_row_formatting(sheet_name)
    totals.sort_all_transactions(sheet_name)
    rebuilt_totals = [totals.compute_period_total(sheet_name, period) for period in periods]
    for period in weekly_periods:
        totals.write_period_total(sheet_name, period)
    totals.write_monthly_total(sheet_name, year, month)
    normalized_rows = totals.normalize_row_formatting(sheet_name)
    return {
        "status": "updated",
        "removed_rows": len(old_total_rows),
        "totals": rebuilt_totals,
        "normalized_rows": normalized_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rapikan subtotal mingguan dan total bulanan ledger serta rincian harian."
    )
    parser.add_argument("--month", required=True, help="Bulan yang diperbaiki dalam format YYYY-MM")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan; tanpa opsi ini hanya dry-run")
    args = parser.parse_args(argv)

    try:
        selected_month = datetime.strptime(args.month, "%Y-%m").date()
    except ValueError:
        parser.error("--month harus berformat YYYY-MM, misalnya 2026-11")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"MODE: {mode} | BULAN: {selected_month:%Y-%m}")
    results = {}
    for sheet_name in [*config.UNIT_USAHA, DAILY_DETAIL_SHEET]:
        try:
            results[sheet_name] = _process_sheet(
                sheet_name, selected_month.year, selected_month.month, args.apply,
            )
        except Exception as error:
            logger.exception("Gagal memproses %s", sheet_name)
            results[sheet_name] = {"status": "failed", "reason": str(error)}

    for sheet_name, result in results.items():
        if result["status"] in {"skip", "failed"}:
            print(f"{sheet_name}: {result['status'].upper()} - {result['reason']}")
        else:
            rewritten = result.get("normalized_rows")
            rewrite_summary = f", baris ditulis ulang={rewritten}" if rewritten is not None else ""
            print(
                f"{sheet_name}: {result['status'].upper()}, "
                f"subtotal/total lama dihapus={result['removed_rows']}"
                f"{rewrite_summary}"
            )
            for total in result["totals"]:
                print(
                    f"  {total.period.label}: pemasukan={total.pemasukan}, "
                    f"pengeluaran={total.pengeluaran}, saldo={total.sisa_saldo_akhir}"
                )
    return int(any(result["status"] in {"skip", "failed"} for result in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())