import argparse
import sys

from app.config import config
from app.core.transaction_id import generate_transaction_id
from app.core.utils import parse_amount
from app.repositories import sheet_repo


DAILY_DETAIL_SHEET = "(2)Rincian Transaksi Harian"
ARCHIVE_SHEET = "_ArsipTransaksi"


def _migration_targets():
    return [
        *((unit, 1, "ledger") for unit in config.UNIT_USAHA),
        (DAILY_DETAIL_SHEET, 4, "daily"),
        (ARCHIVE_SHEET, 0, "archive"),
    ]


def _product_unit(product):
    text = str(product or "").lower()
    if "lele" in text or "ikan" in text:
        return "(6)Perikanan "
    if "ayam" in text or "telur" in text:
        return "(4)Ternak Ayam Fazza"
    if "anggur" in text:
        return "(3)Pertanian "
    return "(3)Pertanian "


def _column_name(column_number):
    result = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _transaction_fields(sheet_name, row, kind):
    try:
        if kind == "ledger":
            transaction_date, product, description = row[1], row[2], row[3]
            quantity, income, expense = row[4], row[6], row[7]
            unit = sheet_name
        elif kind == "daily":
            transaction_date, product, description = row[0], row[1], row[2]
            quantity, income, expense = row[3], row[5], row[6]
            unit = _product_unit(product)
        else:
            unit = row[0]
            transaction_date, product, description = row[2], row[3], row[4]
            quantity, income, expense = row[5], row[7], row[8]
        nominal = parse_amount(income) or parse_amount(expense)
        if not str(product or "").strip() or nominal <= 0:
            return None
        return transaction_date, product, quantity, nominal, description, unit
    except (IndexError, TypeError, ValueError):
        return None


def migrate_transaction_ids(apply=False, _details=False):
    plans = []
    counts = {}
    skipped_invalid_dates = {}
    for sheet_name, header_index, kind in _migration_targets():
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if not worksheet:
            counts[sheet_name] = 0
            continue
        values = sheet_repo._retry_google_call(worksheet.get_all_values)
        if len(values) <= header_index:
            counts[sheet_name] = 0
            continue
        headers = list(values[header_index])
        try:
            tx_column = next(
                index for index, value in enumerate(headers)
                if str(value).strip().casefold() == "transaction id"
            )
        except StopIteration:
            tx_column = len(headers)
            headers.append("Transaction ID")
            if apply:
                sheet_repo._retry_google_call(
                    worksheet.update_cell, header_index + 1, tx_column + 1, "Transaction ID"
                )

        sheet_plans = []
        skipped_count = 0
        for row_number, row in enumerate(values[header_index + 1:], start=header_index + 2):
            fields = _transaction_fields(sheet_name, row, kind)
            if not fields:
                continue
            existing_id = str(row[tx_column]).strip() if tx_column < len(row) else ""
            if existing_id:
                continue
            try:
                transaction_id = generate_transaction_id("", *fields)
            except ValueError:
                skipped_count += 1
                continue
            sheet_plans.append((row_number, tx_column + 1, transaction_id))
        counts[sheet_name] = len(sheet_plans)
        skipped_invalid_dates[sheet_name] = skipped_count
        plans.extend((sheet_name, worksheet, *plan) for plan in sheet_plans)

    if apply:
        updates_by_sheet = {}
        for sheet_name, worksheet, row_number, column_number, transaction_id in plans:
            batch = updates_by_sheet.setdefault(sheet_name, {"worksheet": worksheet, "updates": []})
            batch["updates"].append({
                "range": f"{_column_name(column_number)}{row_number}",
                "values": [[transaction_id]],
            })
        for sheet_name, batch in updates_by_sheet.items():
            sheet_repo._retry_google_call(
                batch["worksheet"].batch_update,
                batch["updates"],
                value_input_option="RAW",
            )
            sheet_repo.invalidate_cache(sheet_name)
    return (counts, skipped_invalid_dates) if _details else counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill Transaction ID pada data transaksi lama.")
    parser.add_argument("--apply", action="store_true", help="Tulis header dan ID; default hanya dry-run")
    args = parser.parse_args(argv)
    try:
        counts, skipped_invalid_dates = migrate_transaction_ids(apply=args.apply, _details=True)
    except Exception as error:
        print(f"Migrasi gagal: {error}", file=sys.stderr)
        return 1
    print("MODE: APPLY" if args.apply else "MODE: DRY RUN (tidak ada perubahan)")
    for sheet_name, count in counts.items():
        print(f"{sheet_name!r}: {count} baris akan diisi")
        skipped = skipped_invalid_dates.get(sheet_name, 0)
        if skipped:
            print(f"  {skipped} baris dilewati: tanggal kosong/tidak valid")
    print(f"Total: {sum(counts.values())} baris")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())