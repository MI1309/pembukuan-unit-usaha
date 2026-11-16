import argparse
import logging

from app.config import config
from app.repositories import sheet_repo
from app.setup_reference_structure import (
    DAILY_DETAIL_HEADERS,
    LEDGER_HEADERS,
    _canonical_daily_detail_rows,
)

logger = logging.getLogger(__name__)


def _layout_plan(sheet_name: str, values: list[list]) -> dict:
    header_index = sheet_repo._detect_header_row_index(sheet_name, values)
    if header_index >= len(values):
        return {"status": "skip", "reason": "header row not found", "swaps": []}

    header = [str(value).strip() for value in values[header_index]]
    if len(header) < 9 or not any("tanggal" == value.casefold() for value in header):
        return {"status": "skip", "reason": "not a 9-column transaction ledger", "swaps": []}

    normalized = [sheet_repo._normalize_header_label(value) for value in header]
    required = {
        "tanggal", "nama barang/jasa /produk", "keterangan transaksi", "kuantitas",
        "satuan", "pemasukan (rp)", "pengeluaran (rp)", "sisa saldo", "transaction id",
    }
    if not required.issubset(set(normalized)):
        return {"status": "skip", "reason": "ledger headers do not match expected schema", "swaps": []}

    swaps = []
    for row_number, row in enumerate(values[header_index + 1:], start=header_index + 2):
        if len(row) > 7 and sheet_repo._TRANSACTION_ID.fullmatch(str(row[7]).strip()):
            id_at_last_column = len(row) > 8 and sheet_repo._TRANSACTION_ID.fullmatch(str(row[8]).strip())
            if not id_at_last_column:
                value_at_last_column = str(row[8]).strip() if len(row) > 8 else ""
                swaps.append((row_number, value_at_last_column, str(row[7]).strip()))

    header_changed = header[:9] != LEDGER_HEADERS
    return {
        "status": "ready",
        "header_index": header_index,
        "header_changed": header_changed,
        "current_header": header[:9],
        "swaps": swaps,
        "last_row": len(values),
    }


def _format_requests(sheet_id: int, header_row_index: int, last_row: int) -> list[dict]:
    requests = [
        {"repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": header_row_index,
                "endRowIndex": header_row_index + 1,
                "startColumnIndex": 0,
                "endColumnIndex": 9,
            },
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
            }},
            "fields": "userEnteredFormat",
        }},
    ]
    if last_row > header_row_index + 1:
        requests.append({"repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": header_row_index + 1,
                "endRowIndex": last_row,
                "startColumnIndex": 0,
                "endColumnIndex": 9,
            },
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.97, "green": 0.98, "blue": 1.0},
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
                "borders": {"bottom": {
                    "style": "SOLID",
                    "color": {"red": 0.86, "green": 0.89, "blue": 0.92},
                }},
            }},
            "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,userEnteredFormat.borders.bottom",
        }})
    if last_row > header_row_index + 1:
        requests.extend([
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": header_row_index + 1, "endRowIndex": last_row, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd mmm yyyy"}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
            }},
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": header_row_index + 1, "endRowIndex": last_row, "startColumnIndex": 4, "endColumnIndex": 5},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}, "horizontalAlignment": "RIGHT"}},
                "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
            }},
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": header_row_index + 1, "endRowIndex": last_row, "startColumnIndex": 5, "endColumnIndex": 8},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"Rp" #,##0'}, "horizontalAlignment": "RIGHT"}},
                "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
            }},
        ])
    widths = [125, 230, 290, 125, 120, 150, 150, 150, 190]
    for column, width in enumerate(widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": column, "endIndex": column + 1},
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }})
    return requests


def _daily_detail_plan(values: list[list]) -> dict:
    sheet_name = "(2)Rincian Transaksi Harian"
    header_index = sheet_repo._detect_header_row_index(sheet_name, values)
    if header_index >= len(values):
        return {"status": "skip", "reason": "header row not found"}

    header = [sheet_repo._normalize_header_label(str(value).strip()) for value in values[header_index]]
    canonical_rows = _canonical_daily_detail_rows(values, header_index)
    if canonical_rows is None:
        return {"status": "skip", "reason": "daily detail headers do not match expected schema"}

    return {
        "status": "ready",
        "header_index": header_index,
        "last_row": len(values),
        "header_changed": header != [sheet_repo._normalize_header_label(v) for v in DAILY_DETAIL_HEADERS],
        "swaps": [],
        "rows": canonical_rows,
    }


def _daily_detail_format_requests(sheet_id: int, header_row_index: int, last_row: int) -> list[dict]:
    requests = [{"repeatCell": {
        "range": {
            "sheetId": sheet_id,
            "startRowIndex": header_row_index,
            "endRowIndex": header_row_index + 1,
            "startColumnIndex": 0,
            "endColumnIndex": 8,
        },
        "cell": {"userEnteredFormat": {
            "backgroundColor": {"red": 0.12, "green": 0.22, "blue": 0.39},
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }},
        "fields": "userEnteredFormat",
    }}]
    if last_row > header_row_index + 1:
        requests.append({"repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": header_row_index + 1,
                "endRowIndex": last_row,
                "startColumnIndex": 0,
                "endColumnIndex": 8,
            },
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": 0.97, "green": 0.98, "blue": 1.0},
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
                "borders": {"bottom": {
                    "style": "SOLID",
                    "color": {"red": 0.86, "green": 0.89, "blue": 0.92},
                }},
            }},
            "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,userEnteredFormat.borders.bottom",
        }})
        requests.extend([
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": header_row_index + 1, "endRowIndex": last_row, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd mmm yyyy"}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
            }},
            {"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": header_row_index + 1, "endRowIndex": last_row, "startColumnIndex": 4, "endColumnIndex": 7},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"Rp" #,##0'}, "horizontalAlignment": "RIGHT"}},
                "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
            }},
        ])
    widths = [125, 230, 290, 125, 140, 160, 160, 190]
    for column, width in enumerate(widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": column, "endIndex": column + 1},
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }})
    return requests


def migrate_unit_ledgers(apply: bool = False) -> dict:
    results = {}
    for sheet_name in config.UNIT_USAHA:
        worksheet = sheet_repo.get_sheet(sheet_name, create=False)
        if not worksheet:
            results[sheet_name] = {"status": "skip", "reason": "worksheet not found"}
            logger.warning("%s: worksheet not found", sheet_name)
            continue

        values = sheet_repo._retry_google_call(worksheet.get_all_values)
        plan = _layout_plan(sheet_name, values)
        results[sheet_name] = plan
        if plan["status"] != "ready":
            logger.warning("%s: skipped: %s", sheet_name, plan["reason"])
            continue

        logger.info(
            "%s: header_changed=%s rows_to_reorder=%d",
            sheet_name, plan["header_changed"], len(plan["swaps"]),
        )
        if not apply:
            continue

        updates = [{
            "range": f"A{plan['header_index'] + 1}:I{plan['header_index'] + 1}",
            "values": [LEDGER_HEADERS],
        }]
        updates.extend({
            "range": f"H{row_number}:I{row_number}",
            "values": [[old_balance, transaction_id]],
        } for row_number, old_balance, transaction_id in plan["swaps"])
        sheet_repo._retry_google_call(
            worksheet.batch_update,
            updates,
            value_input_option="USER_ENTERED",
        )
        sheet_repo._retry_google_call(
            worksheet.spreadsheet.batch_update,
            {"requests": _format_requests(worksheet.id, plan["header_index"], plan["last_row"])},
        )
        sheet_repo.invalidate_cache(sheet_name)
        plan["status"] = "updated"

    daily_name = "(2)Rincian Transaksi Harian"
    daily_sheet = sheet_repo.get_sheet(daily_name, create=False)
    if not daily_sheet:
        results[daily_name] = {"status": "skip", "reason": "worksheet not found"}
    else:
        daily_plan = _daily_detail_plan(sheet_repo._retry_google_call(daily_sheet.get_all_values))
        results[daily_name] = daily_plan
        if daily_plan["status"] == "ready" and apply:
            daily_sheet.batch_update(
                [{
                    "range": f"A{daily_plan['header_index'] + 1}:H{daily_plan['last_row']}",
                    "values": [DAILY_DETAIL_HEADERS, *daily_plan["rows"]],
                }],
                value_input_option="USER_ENTERED",
            )
            sheet_repo._retry_google_call(
                daily_sheet.spreadsheet.batch_update,
                {"requests": _daily_detail_format_requests(
                    daily_sheet.id, daily_plan["header_index"], daily_plan["last_row"],
                )},
            )
            sheet_repo.invalidate_cache(daily_name)
            daily_plan["status"] = "updated"

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rapikan header, kolom, dan format ledger unit serta rincian transaksi harian.")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan; tanpa opsi ini hanya dry-run")
    args = parser.parse_args(argv)
    results = migrate_unit_ledgers(apply=args.apply)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"MODE: {mode}")
    skipped = 0
    for sheet_name, result in results.items():
        if result["status"] == "skip":
            skipped += 1
            print(f"{sheet_name!r}: SKIP - {result['reason']}")
        else:
            print(
                f"{sheet_name!r}: header_changed={result['header_changed']}, "
                f"rows_to_reorder={len(result['swaps'])}, status={result['status']}"
            )
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
