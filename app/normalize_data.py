"""Safely normalize legacy transaction rows in Google Sheets."""

import argparse
import hashlib
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from app.config import config
from app.repositories import sheet_repo
from app.services import periodic_total_service
from app.setup_reference_structure import DAILY_DETAIL_HEADERS, LEDGER_HEADERS

logger = logging.getLogger(__name__)

DAILY_SHEET = "(2)Rincian Transaksi Harian"
ALL_SHEETS = [*config.UNIT_USAHA, DAILY_SHEET]
BACKUP_DIR = Path(__file__).resolve().parents[1] / "data_backups"
TX_ID_PATTERN = re.compile(r"^\d{8}-[A-Z]{3}-[0-9a-fA-F]{8}$")
UNIT_CODES = {
    "(3)pertanian": "PRT",
    "pertanian": "PRT",
    "(4)ternak ayam fazza": "TAF",
    "ternak ayam fazza": "TAF",
    "(5)ternak ayam emran": "TAE",
    "ternak ayam emran": "TAE",
    "(6)perikanan": "IKN",
    "perikanan": "IKN",
    "(7)pabrik pupuk": "PPK",
    "pabrik pupuk": "PPK",
    DAILY_SHEET.casefold(): "RTH",
}
MONTHS = {
    "jan": 1, "januari": 1,
    "feb": 2, "februari": 2,
    "mar": 3, "maret": 3,
    "apr": 4, "april": 4,
    "mei": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "agu": 8, "agt": 8, "ags": 8, "aug": 8, "agustus": 8,
    "sep": 9, "september": 9,
    "okt": 10, "oct": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "des": 12, "dec": 12, "desember": 12,
}


def parse_flexible_date(date_str: str) -> str:
    """Parse common Indonesian/ISO date values and return YYYY-MM-DD."""
    if isinstance(date_str, datetime):
        return date_str.date().isoformat()
    if isinstance(date_str, date):
        return date_str.isoformat()

    text = str(date_str or "").strip()
    if not text:
        raise ValueError("Tanggal kosong")

    for pattern in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass

    localized = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if localized:
        month = MONTHS.get(localized.group(2).casefold())
        if month:
            try:
                return date(int(localized.group(3)), month, int(localized.group(1))).isoformat()
            except ValueError:
                pass

    raise ValueError(f"Tanggal tidak dikenali: {text}")


def _parse_money(value) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    text = str(value or "").strip()
    if not text or text in {"-", "—", "–"}:
        return 0
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("() ").casefold().replace("rp", "").replace("idr", "")
    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"[+-]?[\d.,]+", text):
        raise ValueError(f"Nominal tidak dikenali: {value}")

    sign = -1 if negative or text.startswith("-") else 1
    text = text.lstrip("+-")
    if "." in text and "," in text:
        decimal_separator = "." if text.rfind(".") > text.rfind(",") else ","
        grouping_separator = "," if decimal_separator == "." else "."
        whole, fraction = text.rsplit(decimal_separator, 1)
        normalized = whole.replace(grouping_separator, "").replace(decimal_separator, "")
        normalized = f"{normalized}.{fraction}"
    elif "," in text or "." in text:
        separator = "," if "," in text else "."
        pieces = text.split(separator)
        is_grouped = len(pieces) > 1 and all(len(piece) == 3 for piece in pieces[1:])
        if is_grouped:
            normalized = "".join(pieces)
        else:
            whole, fraction = text.rsplit(separator, 1)
            normalized = f"{whole.replace(separator, '')}.{fraction}"
    else:
        normalized = text

    try:
        amount = Decimal(normalized).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ValueError(f"Nominal tidak dikenali: {value}") from error
    return sign * int(amount)


def _row_has_subtotal_marker(row: list) -> bool:
    return any(
        "[subtotal" in str(value or "").casefold()
        or "subtotal minggu" in str(value or "").casefold()
        or "total bulan" in str(value or "").casefold()
        for value in row
    )


def _unit_code(sheet_name: str) -> str:
    normalized = re.sub(r"\s+", " ", str(sheet_name or "").strip()).casefold()
    return UNIT_CODES.get(normalized, "UNK")


def generate_legacy_tx_id(row: list, unit_code: str = "UNK") -> str:
    """Generate a repeatable legacy ID from date, name, description, and amounts."""
    normalized = list(row) + [""] * max(0, 9 - len(row))
    transaction_date = parse_flexible_date(normalized[0])
    name = re.sub(r"\s+", " ", str(normalized[1] or "").strip()).casefold()
    description = re.sub(r"\s+", " ", str(normalized[2] or "").strip()).casefold()
    income = _parse_money(normalized[5])
    expense = _parse_money(normalized[6])
    payload = "|".join((transaction_date, name, description, str(income), str(expense)))
    hash8 = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    date_part = transaction_date.replace("-", "")
    code = re.sub(r"[^A-Z]", "", str(unit_code).upper())[:3].ljust(3, "X")
    return f"{date_part}-{code}-{hash8}"


def clean_row_data(row: list, unit_code: str = "UNK", column_count: int = 9) -> list:
    """Return a 9-column unit row or an 8-column daily row."""
    source = [str(value or "").strip() for value in row]
    if column_count not in {8, 9}:
        raise ValueError(f"Lebar schema tidak didukung: {column_count}")
    if len(source) > column_count + 1:
        overflow = [value for value in source[column_count + 1:] if value]
        if overflow and not any(TX_ID_PATTERN.fullmatch(value) for value in overflow):
            raise ValueError(f"Ada nilai non-kosong di luar schema: {overflow!r}")

    tx_index = next((index for index, value in enumerate(source) if TX_ID_PATTERN.fullmatch(value)), None)
    if tx_index == column_count and len(source) > column_count and not source[1]:
        source = source[:1] + source[2:]
        tx_index = 8

    date_index = next((
        index for index, value in enumerate(source)
        if value and _can_parse_date(value)
    ), None)
    if date_index is None:
        raise ValueError("Baris transaksi tidak memiliki tanggal yang valid")

    data_width = column_count - 1
    cleaned = (source[:data_width] + [""] * data_width)[:data_width] + [""]
    if date_index != 0:
        if date_index < data_width:
            cleaned[date_index] = ""
        cleaned[0] = source[date_index]
    cleaned[0] = parse_flexible_date(cleaned[0])

    if tx_index is not None:
        transaction_id = source[tx_index]
        if tx_index < data_width:
            cleaned[tx_index] = ""
        if column_count == 9 and tx_index == 7 and len(source) > 8 and source[8] != transaction_id:
            cleaned[7] = source[8]
        cleaned[-1] = transaction_id

    money_columns = (5, 6, 7) if column_count == 9 else (4, 5, 6)
    for index in money_columns:
        cleaned[index] = _parse_money(cleaned[index])

    if not cleaned[-1]:
        cleaned[-1] = generate_legacy_tx_id(cleaned, unit_code)
    return cleaned


def _can_parse_date(value) -> bool:
    try:
        parse_flexible_date(value)
        return True
    except ValueError:
        return False


def _prepare_sheet(sheet_name: str) -> dict:
    worksheet = sheet_repo.get_sheet(sheet_name, create=False)
    if not worksheet:
        return {"sheet": sheet_name, "status": "skip", "reason": "worksheet not found"}

    values = sheet_repo._retry_google_call(worksheet.get_all_values)
    if not values:
        return {"sheet": sheet_name, "status": "skip", "reason": "worksheet is empty"}

    header_index = sheet_repo._detect_header_row_index(sheet_name, values)
    if header_index >= len(values):
        return {"sheet": sheet_name, "status": "skip", "reason": "header not found"}

    headers = DAILY_DETAIL_HEADERS if sheet_name == DAILY_SHEET else LEDGER_HEADERS
    column_count = len(headers)
    if not any(str(cell).strip().casefold() in {"tanggal", "tgl"} for cell in values[header_index]):
        return {"sheet": sheet_name, "status": "skip", "reason": "recognized header missing Tanggal"}

    rows = []
    removed_blank = 0
    removed_totals = 0
    errors = []
    for source_row_number, row in enumerate(values[header_index + 1:], start=header_index + 2):
        if not any(str(value or "").strip() for value in row):
            removed_blank += 1
            continue
        if _row_has_subtotal_marker(row):
            removed_totals += 1
            continue
        try:
            rows.append(clean_row_data(row, _unit_code(sheet_name), column_count))
        except ValueError as error:
            errors.append(f"baris {source_row_number}: {error}")

    if errors:
        return {
            "sheet": sheet_name, "status": "error", "reason": "; ".join(errors),
            "header_index": header_index, "raw_values": values,
        }

    rows.sort(key=lambda clean_row: (clean_row[0], clean_row[-1]))
    return {
        "sheet": sheet_name,
        "status": "ready",
        "header_index": header_index,
        "headers": headers,
        "column_count": column_count,
        "rows": rows,
        "raw_values": values,
        "transaction_count": len(rows),
        "removed_blank": removed_blank,
        "removed_totals": removed_totals,
        "months": sorted({row[0][:7] for row in rows}),
    }


def _cell_data(value) -> dict:
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value or "")}}


def _rewrite_sheet(plan: dict) -> None:
    worksheet = sheet_repo.get_sheet(plan["sheet"], create=False)
    if not worksheet:
        raise RuntimeError(f"Worksheet tidak ditemukan saat menulis: {plan['sheet']}")

    header_index = plan["header_index"]
    old_row_count = len(plan["raw_values"])
    column_count = plan.get("column_count", len(plan["headers"]))
    old_column_count = max(column_count, max((len(row) for row in plan["raw_values"]), default=column_count))
    requests = []
    if old_row_count > header_index + 1:
        requests.append({"updateCells": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": header_index + 1,
                "endRowIndex": old_row_count,
                "startColumnIndex": 0,
                "endColumnIndex": old_column_count,
            },
            "fields": "userEnteredValue",
        }})
    if old_column_count > column_count:
        requests.append({"updateCells": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": header_index,
                "endRowIndex": header_index + 1,
                "startColumnIndex": column_count,
                "endColumnIndex": old_column_count,
            },
            "fields": "userEnteredValue",
        }})

    requests.append({"updateCells": {
        "start": {"sheetId": worksheet.id, "rowIndex": header_index, "columnIndex": 0},
        "rows": [{"values": [_cell_data(value) for value in plan["headers"]]}],
        "fields": "userEnteredValue",
    }})
    if plan["rows"]:
        requests.append({"updateCells": {
            "start": {"sheetId": worksheet.id, "rowIndex": header_index + 1, "columnIndex": 0},
            "rows": [{"values": [_cell_data(value) for value in row]} for row in plan["rows"]],
            "fields": "userEnteredValue",
        }})

    sheet_repo._retry_google_call(worksheet.spreadsheet.batch_update, {"requests": requests})
    sheet_repo.invalidate_cache(plan["sheet"])


def _write_backup(plans: list[dict]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = BACKUP_DIR / f"normalize_data_{timestamp}.json"
    backup_data = {
        plan["sheet"]: plan.get("raw_values", [])
        for plan in plans
        if plan.get("status") == "ready"
    }
    backup_path.write_text(json.dumps(backup_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup_path


def _rebuild_totals(sheet_names: list[str], months: set[str]) -> list[str]:
    errors = []
    for year_month in sorted(months):
        year, month = (int(value) for value in year_month.split("-"))
        for week_index in range(1, 5):
            results = periodic_total_service.process_all_units_period(
                year, month, period_kind="week", week_index=week_index,
                force=True, unit_names=sheet_names,
            )
            errors.extend(f"{name} week {week_index}: {result}" for name, result in results.items() if isinstance(result, Exception))
        results = periodic_total_service.process_all_units_period(
            year, month, period_kind="month", force=True, unit_names=sheet_names,
        )
        errors.extend(f"{name} month: {result}" for name, result in results.items() if isinstance(result, Exception))
    return errors


def normalize_sheet(sheet_name: str, apply: bool = False) -> dict:
    """Prepare one sheet and optionally back it up, rewrite it, and rebuild totals."""
    plan = _prepare_sheet(sheet_name)
    if plan["status"] != "ready" or not apply:
        return plan

    backup_path = _write_backup([plan])
    _rewrite_sheet(plan)
    total_errors = _rebuild_totals([sheet_name], set(plan["months"]))
    return {
        **{key: value for key, value in plan.items() if key != "raw_values"},
        "status": "updated",
        "backup": str(backup_path),
        "total_errors": total_errors,
    }


def _print_result(result: dict, mode: str) -> None:
    print(
        f"{mode} {result['sheet']}: status={result['status']}"
        f", transaksi={result.get('transaction_count', 0)}"
        f", baris kosong dihapus={result.get('removed_blank', 0)}"
        f", subtotal lama dihapus={result.get('removed_totals', 0)}"
    )
    if result.get("reason"):
        print(f"  detail: {result['reason']}")
    if result.get("backup"):
        print(f"  backup: {result['backup']}")
    for row in result.get("rows", [])[:5]:
        print(f"  preview: {row!r}")
    if result.get("transaction_count", 0) > 5:
        print(f"  preview: {result['transaction_count'] - 5} baris lain tidak ditampilkan")
    for error in result.get("total_errors", []):
        print(f"  peringatan total: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalisasi data transaksi lama di Google Sheets.")
    parser.add_argument("--sheet", help="Nama sheet tertentu; default memproses seluruh ledger")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan; tanpa opsi ini hanya dry-run")
    args = parser.parse_args(argv)

    if args.sheet and args.sheet not in ALL_SHEETS:
        parser.error(f"Sheet tidak termasuk ledger yang diizinkan: {args.sheet}")
    sheet_names = [args.sheet] if args.sheet else ALL_SHEETS
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"MODE: {mode}")

    plans = [_prepare_sheet(sheet_name) for sheet_name in sheet_names]
    errors = [plan for plan in plans if plan["status"] == "error"]
    if args.apply and not errors:
        ready_plans = [plan for plan in plans if plan["status"] == "ready"]
        if ready_plans:
            backup_path = _write_backup(ready_plans)
            applied = []
            for plan in ready_plans:
                _rewrite_sheet(plan)
                applied.append(plan)
            months = {month for plan in applied for month in plan["months"]}
            total_errors = _rebuild_totals([plan["sheet"] for plan in applied], months)
            for plan in plans:
                if plan in applied:
                    plan["status"] = "updated"
                    plan["backup"] = str(backup_path)
                    plan["total_errors"] = total_errors

    for plan in plans:
        _print_result(plan, mode)
    return 1 if errors or any(plan.get("total_errors") for plan in plans) else 0


if __name__ == "__main__":
    raise SystemExit(main())