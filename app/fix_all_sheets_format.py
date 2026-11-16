import argparse
import logging
from collections import defaultdict

from app.config import config
from app.repositories import sheet_repo
from app.services import periodic_total_service as totals

logger = logging.getLogger(__name__)
DAILY_DETAIL_SHEET = "(2)Rincian Transaksi Harian"
TARGET_SHEETS = [*config.UNIT_USAHA, DAILY_DETAIL_SHEET]


def _legacy_total_rows(values: list[list], header_index: int) -> list[int]:
    markers = ("[SUBTOTAL", "SUBTOTAL", "TOTAL BULAN")
    return [
        index + 1
        for index, row in enumerate(values)
        if index > header_index and any(
            any(marker in str(value).strip().upper() for marker in markers)
            for value in row
        )
    ]


def _transactions_by_month(sheet_name: str) -> dict[tuple[int, int], set[int]]:
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return {}
    values = worksheet.get_all_values()
    if not values:
        return {}

    columns = totals._resolved_columns(sheet_name, values)
    date_index = columns["date"] if columns["date"] is not None else 0
    result: dict[tuple[int, int], set[int]] = defaultdict(set)
    for index, row in enumerate(values):
        if index <= columns["header"] or totals.is_subtotal_row(row):
            continue
        row_date = totals._row_transaction_date(row, date_index)
        if row_date:
            result[(row_date.year, row_date.month)].add(row_date.day)
    return dict(result)


def _process_sheet(sheet_name: str, apply: bool) -> dict:
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return {"status": "skip", "reason": "worksheet not found"}

    values = worksheet.get_all_values()
    if not values:
        return {"status": "skip", "reason": "worksheet empty"}
    columns = totals._resolved_columns(sheet_name, values)
    old_total_rows = _legacy_total_rows(values, columns["header"])

    if not apply:
        months = _transactions_by_month(sheet_name)
        planned_totals = sum(
            len({(day - 1) // 7 for day in days}) + 1
            for days in months.values()
        )
        return {
            "status": "planned",
            "removed_rows": len(old_total_rows),
            "months": len(months),
            "active_weeks": planned_totals,
        }

    if old_total_rows:
        sheet_repo.delete_rows_bottom_up(sheet_name, old_total_rows)
    totals.normalize_sheet_rows(sheet_name)
    totals.sort_all_transactions(sheet_name)
    transactions_by_month = _transactions_by_month(sheet_name)
    written = 0

    for year, month in sorted(transactions_by_month):
        days = transactions_by_month[(year, month)]
        periods = totals.get_period_boundaries(year, month)
        for period in periods:
            if period.kind == "week":
                has_transactions = any(
                    period.start_date.day <= day <= period.end_date.day
                    for day in days
                )
                if not has_transactions:
                    continue
                totals.write_period_total(sheet_name, period, force=True)
            else:
                totals.write_monthly_total(sheet_name, year, month, force=True)
            written += 1

    return {
        "status": "updated",
        "removed_rows": len(old_total_rows),
        "months": len(transactions_by_month),
        "written_totals": written,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rapikan format transaksi dan bangun ulang subtotal di semua ledger."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Terapkan perubahan; tanpa opsi ini hanya tampilkan rencana",
    )
    args = parser.parse_args(argv)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"MODE: {mode}")
    failed = False
    for sheet_name in TARGET_SHEETS:
        try:
            result = _process_sheet(sheet_name, args.apply)
        except Exception as error:
            logger.exception("Gagal memproses %s", sheet_name)
            result = {"status": "failed", "reason": str(error)}
        details = ", ".join(
            f"{key}={value}" for key, value in result.items() if key != "status"
        )
        print(f"{sheet_name}: {result['status'].upper()} {details}".rstrip())
        failed = failed or result["status"] == "failed"
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())