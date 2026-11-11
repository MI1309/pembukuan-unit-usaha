import calendar
import re
from datetime import date, datetime, timedelta
from threading import RLock

from app.config import config
from app.core.groq_ai import analyze_sales
from app.core.utils import format_rupiah, format_tanggal_id, now_tz, parse_amount
from app.repositories import sheet_repo
from app.core.whatsapp import send_whatsapp

_SALES_LOCK = RLock()
MONTH_ALIASES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "jun": 6,
    "jul": 7, "agu": 8, "agt": 8, "sep": 9, "okt": 10, "nov": 11, "des": 12,
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10,
    "november": 11, "desember": 12,
}


def get_unit_sheet(produk_nama: str) -> str:
    product = produk_nama.lower()
    if "lele" in product or "ikan" in product:
        return "(6)Perikanan "
    if "ayam" in product or "telur" in product:
        return "(4)Ternak Ayam Fazza"
    if "anggur" in product:
        return "(3)Pertanian "
    return "(3)Pertanian "


def _resolve_unit_sheet(value: str) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    aliases = {
        "(3)pertanian": ["pertanian", "pertanian anggur", "pertanian jagung", "pertanian padi", "pertanian melon"],
        "(4)ternak ayam fazza": ["ternak ayam fazza", "ayam fazza"],
        "(5)ternak ayam emran": ["ternak ayam emran", "ayam emran"],
        "(6)perikanan": ["perikanan", "perikanan emran"],
        "(7)pabrik pupuk": ["pabrik pupuk"],
    }
    for unit in config.UNIT_USAHA:
        normalized_unit = re.sub(r"\s+", " ", unit.strip().lower())
        if text == normalized_unit:
            return unit
        for canonical, unit_aliases in aliases.items():
            if normalized_unit == canonical and text in unit_aliases:
                return unit
    return None


def _resolve_product(raw: dict) -> dict | None:
    product_text = f"{raw.get('produk', '')} {raw.get('keterangan', '')}".lower()
    for item in config.KATALOG:
        if any(keyword in product_text for keyword in item["kunci"]):
            return item
    return None


def _normalise_sale(raw: dict) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, None
    is_sale = raw.get("is_penjualan") is not False and str(raw.get("jenis", "")).strip().lower() not in {"pembelian", "pengeluaran"}
    product = _resolve_product(raw) if is_sale else None
    product_name = product["nama"] if product else str(raw.get("produk", "Barang pembelian")).strip()
    if is_sale and not product:
        return None, f"{raw.get('produk', 'Produk tidak dikenal')} — produk penjualan tidak dikenal"
    unit_sheet = get_unit_sheet(product_name) if is_sale else _resolve_unit_sheet(raw.get("unit_usaha", ""))
    if not is_sale and not unit_sheet:
        return None, f"{product_name} — sebutkan unit usaha pembelian (Pertanian, Ternak Ayam Fazza, Ternak Ayam Emran, Perikanan, atau Pabrik Pupuk)"
    try:
        quantity = float(raw.get("kuantitas") or 0)
    except (TypeError, ValueError):
        quantity = 0
    unit_price = parse_amount(raw.get("harga_satuan"))
    raw_total = parse_amount(raw.get("total_harga") or raw.get("penjualan"))
    quantity_unit = product["satuan"] if product else str(raw.get("satuan") or "").strip()
    total = round(unit_price * quantity) if unit_price and quantity else raw_total
    if not is_sale and (quantity <= 0 or not quantity_unit):
        return None, f"{product_name} — pembelian wajib mencantumkan kuantitas dan satuan"
    if not total:
        return None, f"{product_name} — harga satuan atau total harus dicantumkan"
    if total <= 0 or quantity <= 0:
        return None, f"{product_name} — jumlah/harga salah"
    if quantity >= 1_000_000 or (is_sale and quantity >= total):
        return None, f"{product_name} {quantity:g} — jumlah tidak wajar atau tertukar dengan harga"
    current_time = now_tz()
    today = current_time.date()
    sale_date = _parse_date(raw.get("tanggal")) or today
    proposed_datetime = current_time.replace(
        year=sale_date.year, month=sale_date.month, day=sale_date.day,
        hour=0, minute=0, second=0, microsecond=0,
    )
    if proposed_datetime - current_time > timedelta(hours=36):
        sale_date = today
    description = str(raw.get("keterangan") or "").strip()
    if not unit_price and quantity:
        unit_price = round(total / quantity)
    return {
        "produk": product_name,
        "kategori": product["kategori"] if product else "PEMBELIAN",
        "satuan": unit_price,
        "unit_kuantitas": quantity_unit,
        "kuantitas": quantity,
        "penjualan": total,
        "is_penjualan": is_sale,
        "keterangan": description,
        "tanggal": sale_date,
        "unit": unit_sheet,
    }, None


def _daily_period_label(month_date: date) -> str:
    month_start = date(month_date.year, month_date.month, 1)
    last_day = calendar.monthrange(month_date.year, month_date.month)[1]
    month_end = date(month_date.year, month_date.month, last_day)
    return f"Periode: {month_start:%d %b %Y} s/d {month_end:%d %b %Y} Semua Unit Usaha"


def _maybe_archive_daily_detail(sale_date: date) -> None:
    sheet = sheet_repo.get_sheet("(2)Rincian Transaksi Harian", create=False)
    if not sheet:
        return
    rows = sheet.get_all_values()
    if len(rows) < 3:
        sheet.update(range_name="A3", values=[[_daily_period_label(sale_date)]])
        return
    try:
        label = str(rows[2][0]).strip()
        match = re.search(r"\b(\d{2})\s+([A-Za-z]{3})\s+(\d{4})\b", label)
        if not match:
            return
        current_month = datetime.strptime(f"{match.group(3)}-{match.group(2)}-01", "%Y-%b-%d").date()
        if current_month.year == sale_date.year and current_month.month == sale_date.month:
            return
        archived_rows = rows[5:]
        if archived_rows:
            sheet_repo.archive_daily_detail(
                current_month.strftime("%Y-%m"),
                [row[:7] for row in archived_rows if len(row) >= 7],
                _daily_period_label(sale_date),
            )
        sheet.update(range_name="A3", values=[[_daily_period_label(sale_date)]])
    except Exception:
        return


def _save_sale(sale: dict) -> str:
    _maybe_archive_daily_detail(sale["tanggal"])
    is_sale = sale.get("is_penjualan", True)
    income = sale["penjualan"] if is_sale else ""
    expense = "" if is_sale else sale["penjualan"]
    balance_change = sale["penjualan"] if is_sale else -sale["penjualan"]
    date_text = _format_sheet_date(sale["tanggal"])
    quantity_text = f"{sale['kuantitas']:g} {sale['unit_kuantitas']}".strip()
    unit_row = [
        "", date_text, sale["produk"],
        sale["keterangan"], quantity_text, sale["satuan"],
        income, expense, balance_change,
    ]
    daily_detail_row = [
        date_text, sale["produk"], sale["keterangan"],
        quantity_text, sale["satuan"], income, expense,
    ]
    sheet_repo.append_rows_atomically([
        (sale["unit"], unit_row),
        ("(2)Rincian Transaksi Harian", daily_detail_row),
    ])
    return None


def _format_sheet_date(value: date) -> str:
    month_names = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
    return f"{value.day} {month_names[value.month - 1]} {value.year}"


def _confirm_text(sale: dict) -> str:
    return (
        "⚠️ *KONFIRMASI TRANSAKSI BESAR*\n━━━━━━━━━━━━━━━━━━\n"
        f"Produk: {sale['produk']}\nJumlah: {sale['kuantitas']:g} {sale['satuan']}\n"
        f"Total: {format_rupiah(sale['penjualan'])}\n━━━━━━━━━━━━━━━━━━\n"
        "Balas *YA* untuk simpan, *BATAL* untuk batal."
    )


def process_sales(message: str, confirmed: bool = False) -> tuple[bool, str, dict | None]:
    parsed = analyze_sales(message)
    raw_items = parsed.get("transaksi", []) if isinstance(parsed, dict) else []
    prepared = []
    failed = []
    for raw in raw_items:
        sale, error = _normalise_sale(raw)
        if error:
            failed.append(error)
        elif sale:
            prepared.append(sale)
    if not prepared and not failed:
        return False, "", None

    large_sales = [sale for sale in prepared if sale["penjualan"] >= config.CONFIRM_THRESHOLD]
    if large_sales and not confirmed:
        return True, _confirm_text(large_sales[0]), {"pending": True}

    saved = []
    with _SALES_LOCK:
        for sale in prepared:
            try:
                _save_sale(sale)
                saved.append(sale)
            except Exception as error:
                failed.append(f"{sale['produk']} — {error}")

    lines = []
    if saved:
        transaction_types = {sale.get("is_penjualan", True) for sale in saved}
        heading = "PENJUALAN / PEMASUKAN" if transaction_types == {True} else (
            "PEMBELIAN / PENGELUARAN" if transaction_types == {False} else "TRANSAKSI"
        )
        lines += [f"✅ *{len(saved)} {heading} BERHASIL DISIMPAN*", "━━━━━━━━━━━━━━━━━━"]
        total = sum(sale["penjualan"] for sale in saved)
        if len(saved) <= 8:
            for index, sale in enumerate(saved, start=1):
                amount_label = "Pemasukan" if sale.get("is_penjualan", True) else "Pengeluaran"
                quantity_text = f"{sale['kuantitas']:g} {sale['unit_kuantitas']} → " if sale["kuantitas"] > 0 else ""
                lines += [f"{index}. *{sale['produk']}*",
                          f"   📅 {format_tanggal_id(datetime.combine(sale['tanggal'], datetime.min.time()))}",
                          f"   ⚖️ {quantity_text}💰 {amount_label}: {format_rupiah(sale['penjualan'])}"]
        else:
            lines.append(f"🧾 {len(saved)} transaksi dicatat. Cek rekap untuk rincian.")
        lines += ["━━━━━━━━━━━━━━━━━━", f"💰 *Total transaksi: {format_rupiah(total)}*"]
    if failed:
        if saved:
            lines.append("")
        lines.append(f"⚠️ *{len(failed)} BARIS TIDAK DISIMPAN*")
        lines.extend(f"• {error}" for error in failed[:10])
        if len(failed) > 10:
            lines.append(f"... dan {len(failed) - 10} baris gagal lainnya")
    return True, "\n".join(lines), {"saved": len(saved)}


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    localized = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if localized:
        month = MONTH_ALIASES.get(localized.group(2).lower())
        if month:
            try:
                return date(int(localized.group(3)), month, int(localized.group(1)))
            except ValueError:
                return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y.%m.%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _transaction_rows() -> list[dict]:
    transactions = []
    records_by_unit = sheet_repo.get_all_records_many(config.UNIT_USAHA)
    for unit, records in records_by_unit.items():
        for row in records:
            sale_date = _parse_date(row.get("Tanggal"))
            if not sale_date:
                continue
            product = str(row.get("Nama Barang/Jasa /Produk", row.get("Produk", ""))).strip()
            if not product or re.match(r"^(sub)?total", product, re.I):
                continue
            income = parse_amount(row.get("Pemasukan (Rp)", row.get("Pemasukan", 0)))
            expense = parse_amount(row.get("Pengeluaran (Rp)", row.get("Pengeluaran", 0)))
            if income <= 0 and expense <= 0:
                continue
            transactions.append({
                "date": sale_date,
                "product": product,
                "unit": str(row.get("Satuan (Rp)", row.get("Satuan", ""))).strip(),
                "quantity": _number(row.get("Kuantitas", 0)),
                "business_unit": re.sub(r"^\(\d+\)\s*", "", unit).strip(),
                "income": income,
                "expense": expense,
            })
    return transactions


def _sales_rows() -> list[dict]:
    return [
        {**row, "amount": row["income"]}
        for row in _transaction_rows()
        if row["income"] > 0
    ]

# private nomor functions
def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return float(parse_amount(value))


def _period_from_message(message: str) -> tuple[str, date]:
    text = message.lower().strip()
    today = now_tz().date()
    if "minggu lalu" in text:
        current_monday = today - timedelta(days=today.weekday())
        return "week", current_monday - timedelta(days=7)
    if re.search(r"minggu\s*(ini|sekarang)|mingguan|\bminggu\b", text):
        return "week", today - timedelta(days=today.weekday())
    if "tahun lalu" in text:
        return "year", date(today.year - 1, 1, 1)
    if re.search(r"tahun\s*(ini|sekarang)|tahunan", text):
        return "year", date(today.year, 1, 1)
    if "bulan lalu" in text:
        previous = today.replace(day=1) - timedelta(days=1)
        return "month", previous.replace(day=1)
    if re.search(r"bulan\s*(ini|sekarang)|bulanan", text):
        return "month", today.replace(day=1)

    match = re.search(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        day = int(match.group(3) or 1)
        if 1 <= month <= 12:
            return ("day", date(year, month, day)) if match.group(3) else ("month", date(year, month, 1))
    match = re.search(r"\b(\d{1,2})[-/](\d{1,2})(?:[-/](\d{4}))?\b", text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3) or today.year)
        try:
            return "day", date(year, month, day)
        except ValueError:
            pass
    match = re.search(r"\b(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?\b", text)
    if match:
        day, month_name, year = int(match.group(1)), match.group(2)[:3], int(match.group(3) or today.year)
        month = MONTH_ALIASES.get(month_name)
        if month:
            try:
                return "day", date(year, month, day)
            except ValueError:
                pass
    month_pattern = "|".join(sorted((re.escape(alias) for alias in MONTH_ALIASES), key=len, reverse=True))
    match = re.search(rf"\b({month_pattern})(?:\s+(\d{{4}}))?\b", text)
    if match:
        month = MONTH_ALIASES[match.group(1)]
        year = int(match.group(2) or today.year)
        try:
            return "month", date(year, month, 1)
        except ValueError:
            pass
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return "year", date(int(match.group(1)), 1, 1)
    if "kemarin" in text:
        return "day", today - timedelta(days=1)
    return "day", today


def _aggregate(rows: list[dict], key) -> dict:
    result = {}
    for row in rows:
        bucket = key(row)
        item = result.setdefault(bucket, {"amount": 0, "quantity": 0, "count": 0, "unit": row["unit"]})
        item["amount"] += row["amount"]
        item["quantity"] += row["quantity"]
        item["count"] += 1
    return result


def _day_report(target: date, rows: list[dict]) -> str:
    selected = [row for row in rows if row["date"] == target]
    title_date = format_tanggal_id(datetime.combine(target, datetime.min.time()))
    if not selected:
        return f"📊 *REKAP PENJUALAN*\n📅 {title_date}\n━━━━━━━━━━━━━━━━━━\n_Belum ada penjualan tercatat._"
    products = _aggregate(selected, lambda row: row["product"])
    lines = ["📊 *REKAP PENJUALAN HARIAN*", f"📅 {title_date}", "━━━━━━━━━━━━━━━━━━"]
    for index, (product, values) in enumerate(sorted(products.items(), key=lambda item: item[1]["amount"], reverse=True), start=1):
        lines += [f"{index}. *{product}*", f"   ⚖️ {values['quantity']:g} {values['unit']} → 💰 {format_rupiah(values['amount'])}"]
    lines += ["━━━━━━━━━━━━━━━━━━", f"💰 *TOTAL:* {format_rupiah(sum(row['amount'] for row in selected))}", f"🧾 {len(selected)} transaksi"]
    return "\n".join(lines)


def _month_report(target: date, rows: list[dict]) -> str:
    selected = [row for row in rows if row["date"].year == target.year and row["date"].month == target.month]
    month_name = config.BULAN[target.month - 1]
    if not selected:
        return f"📊 *REKAP PENJUALAN BULANAN*\n📅 {month_name} {target.year}\n━━━━━━━━━━━━━━━━━━\n_Belum ada penjualan tercatat._"
    products = _aggregate(selected, lambda row: row["product"])
    days = _aggregate(selected, lambda row: row["date"])
    lines = ["📊 *REKAP PENJUALAN BULANAN*", f"📅 {month_name} {target.year}", "━━━━━━━━━━━━━━━━━━", "*Per Produk:*"]
    for index, (product, values) in enumerate(sorted(products.items(), key=lambda item: item[1]["amount"], reverse=True), start=1):
        lines += [f"{index}. *{product}*", f"   ⚖️ {values['quantity']:g} {values['unit']} → 💰 {format_rupiah(values['amount'])}"]
    top_days = sorted(days.items(), key=lambda item: item[1]["amount"], reverse=True)[:5]
    if top_days:
        lines += ["━━━━━━━━━━━━━━━━━━", f"🔥 *Top {len(top_days)} Hari:*"]
        for index, (day, values) in enumerate(top_days, start=1):
            lines.append(f"{index}. {format_tanggal_id(datetime.combine(day, datetime.min.time()))} → {format_rupiah(values['amount'])} ({values['count']} trx)")
    lines += ["━━━━━━━━━━━━━━━━━━", f"💰 *TOTAL BULAN:* {format_rupiah(sum(row['amount'] for row in selected))}",
              f"🧾 {len(selected)} transaksi", f"📆 {len(days)} hari aktif"]
    return "\n".join(lines)


def _week_report(start: date, rows: list[dict]) -> str:
    end = start + timedelta(days=6)
    selected = [row for row in rows if start <= row["date"] <= end]
    period = (
        f"{format_tanggal_id(datetime.combine(start, datetime.min.time()))}"
        f" – {format_tanggal_id(datetime.combine(end, datetime.min.time()))}"
    )
    if not selected:
        return f"📊 *REKAP TRANSAKSI MINGGUAN*\n📅 {period}\n━━━━━━━━━━━━━━━━━━\n_Belum ada transaksi tercatat._"

    income_total = sum(row["income"] for row in selected)
    expense_total = sum(row["expense"] for row in selected)
    unit_totals = {}
    for row in selected:
        totals = unit_totals.setdefault(row["business_unit"], {"income": 0, "expense": 0, "count": 0})
        totals["income"] += row["income"]
        totals["expense"] += row["expense"]
        totals["count"] += 1

    lines = ["📊 *REKAP TRANSAKSI MINGGUAN*", f"📅 {period}", "━━━━━━━━━━━━━━━━━━", "*Per Unit Usaha:*"]
    for unit, totals in sorted(unit_totals.items()):
        lines += [
            f"• *{unit}* ({totals['count']} transaksi)",
            f"  Pemasukan: {format_rupiah(totals['income'])} | Pengeluaran: {format_rupiah(totals['expense'])}",
        ]
    lines += [
        "━━━━━━━━━━━━━━━━━━",
        f"📥 *TOTAL PEMASUKAN:* {format_rupiah(income_total)}",
        f"📤 *TOTAL PENGELUARAN:* {format_rupiah(expense_total)}",
        f"💰 *TOTAL NOMINAL SEMUA TRANSAKSI:* {format_rupiah(income_total + expense_total)}",
        f"⚖️ *SELISIH:* {format_rupiah(income_total - expense_total)}",
        f"🧾 {len(selected)} transaksi",
    ]
    return "\n".join(lines)


def _year_report(target: date, rows: list[dict]) -> str:
    selected = [row for row in rows if row["date"].year == target.year]
    if not selected:
        return f"📊 *REKAP PENJUALAN TAHUNAN*\n📅 Tahun {target.year}\n━━━━━━━━━━━━━━━━━━\n_Belum ada penjualan tercatat._"
    products = _aggregate(selected, lambda row: row["product"])
    months = _aggregate(selected, lambda row: row["date"].strftime("%Y-%m"))
    days = _aggregate(selected, lambda row: row["date"])
    lines = ["📊 *REKAP PENJUALAN TAHUNAN*", f"📅 Tahun {target.year}", "━━━━━━━━━━━━━━━━━━", "*Per Produk:*"]
    for index, (product, values) in enumerate(sorted(products.items(), key=lambda item: item[1]["amount"], reverse=True), start=1):
        lines += [f"{index}. *{product}*", f"   ⚖️ {values['quantity']:g} {values['unit']} → 💰 {format_rupiah(values['amount'])}", f"   📦 {values['count']} transaksi"]
    top_months = sorted(months.items(), key=lambda item: item[1]["amount"], reverse=True)[:3]
    if top_months:
        lines += ["━━━━━━━━━━━━━━━━━━", f"🔥 *Top {len(top_months)} Bulan:"]
        for index, (period, values) in enumerate(top_months, start=1):
            month = int(period[5:7])
            lines.append(f"{index}. {config.BULAN[month - 1]} {target.year} → {format_rupiah(values['amount'])} ({values['count']} trx)")
    top_days = sorted(days.items(), key=lambda item: item[1]["amount"], reverse=True)[:5]
    if top_days:
        lines += ["━━━━━━━━━━━━━━━━━━", f"⭐ *Top {len(top_days)} Hari:"]
        for index, (day, values) in enumerate(top_days, start=1):
            lines.append(f"{index}. {format_tanggal_id(datetime.combine(day, datetime.min.time()))} → {format_rupiah(values['amount'])} ({values['count']} trx)")
    lines += ["━━━━━━━━━━━━━━━━━━", f"💰 *TOTAL TAHUN:* {format_rupiah(sum(row['amount'] for row in selected))}",
              f"🧾 {len(selected)} transaksi", f"📆 {len(months)} bulan aktif", f"📅 {len(days)} hari aktif"]
    return "\n".join(lines)


def process_rekap(message: str) -> str | None:
    if not re.search(r"\brekap\b", message, re.I):
        return None
    period_type, target = _period_from_message(message)
    if period_type == "week":
        return _week_report(target, _transaction_rows())
    rows = _sales_rows()
    if period_type == "year":
        return _year_report(target, rows)
    if period_type == "month":
        return _month_report(target, rows)
    return _day_report(target, rows)


def _replace_recap_rows(sheet_name: str, period: str, selected: list[dict]) -> None:
    worksheet = sheet_repo.get_sheet(sheet_name)
    if not worksheet.get_all_values():
        period_header = "Tanggal" if sheet_name == "_RekapHarian" else "Bulan"
        sheet_repo.append_row(sheet_name, [period_header, "Produk", "Kuantitas", "Satuan", "Total (Rp)", "Jumlah Transaksi"])
    records = sheet_repo.get_all_records(sheet_name)
    matching_rows = [index + 2 for index, row in enumerate(records)
                     if str(row.get("Tanggal", row.get("Bulan", ""))).strip() == period]
    for row_number in reversed(matching_rows):
        sheet_repo.delete_row(sheet_name, row_number)
    if not selected:
        sheet_repo.append_row(sheet_name, [period, "-", 0, "", 0, 0])
        return
    by_product = _aggregate(selected, lambda row: row["product"])
    total_quantity = sum(row["quantity"] for row in selected)
    total_amount = sum(row["amount"] for row in selected)
    total_count = len(selected)
    for product, values in by_product.items():
        sheet_repo.append_row(sheet_name, [
            period, product, values["quantity"], values["unit"], values["amount"], values["count"],
        ])
    sheet_repo.append_row(sheet_name, [period, "TOTAL", total_quantity, "", total_amount, total_count])


def save_daily_recap(target: date | None = None) -> None:
    target = target or now_tz().date()
    period = target.isoformat()
    selected = [row for row in _sales_rows() if row["date"] == target]
    _replace_recap_rows("_RekapHarian", period, selected)


def save_monthly_recap(target: date | None = None) -> None:
    target = target or now_tz().date()
    period = target.strftime("%Y-%m")
    selected = [row for row in _sales_rows()
                if row["date"].year == target.year and row["date"].month == target.month]
    _replace_recap_rows("_RekapBulanan", period, selected)


def send_daily_recap() -> str:
    target = now_tz().date()
    message = _day_report(target, _sales_rows())
    send_whatsapp(config.ADMIN_NOTIFY, message)
    save_daily_recap(target)
    from app.services.webhook_state import log_event
    log_event("SYSTEM", "send_daily_recap", "REKAP_HARIAN_OK")
    return message


def send_previous_month_recap() -> str:
    today = now_tz().date()
    target = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    message = _month_report(target, _sales_rows())
    send_whatsapp(config.ADMIN_NOTIFY, message)
    save_monthly_recap(target)
    from app.services.webhook_state import log_event
    log_event("SYSTEM", "send_monthly_recap", "REKAP_BULANAN_OK")
    return message


def handle_help_command(message: str) -> str | None:
    text = message.strip().lower()
    if not re.fullmatch(r"(help|bantuan|panduan|cara|menu|bisa apa|\?|halo|hai|hi)", text):
        return None
    return (
        "🤖 *PANDUAN BOT TRANSAKSI*\n_" + config.COMPANY + "_\n━━━━━━━━━━━━━━━━━━\n"
        "\n📝 *1. CATAT PENJUALAN*\nKirim pesan seperti ini:\n"
        "Format: `penj [nama barang] | [keterangan] | [kuantitas] | [satuan] | [harga satuan]`.\n"
        "_penj telur ayam fazza | jual ke warung A | 100 | butir | 1500_\n"
        "_penj ayam jantan | pesan toko | 40 | ekor | 35000_\n"
        "_penj anggur helio | order pasaran | 15 | kg | 30000_\n"
        "_penj lele fazza | pelanggan minggu ini | 50 | ekor | 18000_\n\n"
        "Alias: `penj` / `jual` = pemasukan; `pembelian` / `beli` / `pemb` = pengeluaran.\n\n"
        "🧾 *PEMBELIAN / PENGELUARAN*\nFormat: `pemb [nama barang/jasa] | [keterangan] | [kuantitas] | [satuan] | [harga satuan] | unit [unit usaha]`.\n"
        "Kategori: listrik, UHK, penyusutan, konsumsi dapur, dan PDAM.\n"
        "_pemb listrik | tagihan November | 1 | bulan | 1200000 | unit Pertanian_\n"
        "_beli UHK | upah harian | 3 | orang | 300000 | unit Pertanian_\n"
        "_pemb penyusutan alat | periode November | 1 | bulan | 500000 | unit Pertanian_\n"
        "_pemb konsumsi dapur | belanja bahan | 1 | paket | 250000 | unit Ternak Ayam Fazza_\n"
        "_pemb PDAM | tagihan November | 1 | bulan | 180000 | unit Pertanian_\n"
        "Unit: Pertanian, Ternak Ayam Fazza, Ternak Ayam Emran, Perikanan, atau Pabrik Pupuk.\n\n"
        "✅ Satu pesan dapat berisi banyak transaksi, biasanya satu transaksi per baris.\n"
        "✅ Tanggal dicatat dengan format tgl, nama bulan singkat, tahun; keterangan mengikuti isi pesan.\n"
        "✅ Kuantitas dicatat bersama unit barang; harga satuan dikalikan kuantitas untuk mengisi pemasukan atau pengeluaran.\n"
        "✅ Harga satuan dipakai untuk menghitung total otomatis: kuantitas × harga satuan.\n\n"
        "📊 *2. REKAP PENJUALAN*\n"
        "• `rekap` → hari ini; `rekap kemarin` → kemarin\n"
        "• `rekap minggu ini` / `rekap minggu lalu` → total pemasukan, pengeluaran, dan semua nominal per unit (Senin–Minggu)\n"
        "• `rekap bulan ini` / `rekap bulan lalu`\n"
        "• `rekap tahun ini` / `rekap tahun lalu` / `rekap 2026`\n"
        "• `rekap jan` → Januari tahun ini; `rekap 2026-10` / `rekap oktober 2026` / `rekap okt 2026`\n"
        "• `rekap 15 okt` / `rekap 15-10-2026`\n\n"
        "🗄️ Admin: ketik `backup` untuk membuat salinan spreadsheet sekarang.\n\n"
        f"⚠️ Transaksi ≥ {format_rupiah(config.CONFIRM_THRESHOLD)} meminta konfirmasi `YA` / `BATAL`.\n\n"
        "👥 *3. FITUR KARYAWAN*\nKetik `help karyawan` untuk panduan karyawan, presensi, dan penggajian.\n"
        "━━━━━━━━━━━━━━━━━━"
    )