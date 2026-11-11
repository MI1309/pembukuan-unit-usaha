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
        return "Perikanan Emran"
    if "ayam" in product or "telur" in product:
        return "Ternak Ayam Fazza"
    if "anggur" in product:
        return "Pertanian Anggur"
    return "Pertanian Jagung"


def _resolve_product(raw: dict) -> dict | None:
    product_text = f"{raw.get('produk', '')} {raw.get('keterangan', '')}".lower()
    for item in config.KATALOG:
        if any(keyword in product_text for keyword in item["kunci"]):
            return item
    return None


def _normalise_sale(raw: dict) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, None
    if raw.get("is_penjualan") is False:
        return None, None
    product = _resolve_product(raw)
    if not product:
        return None, f"{raw.get('produk', 'Produk tidak dikenal')} — produk tidak dikenal"
    try:
        quantity = float(raw.get("kuantitas") or 0)
    except (TypeError, ValueError):
        quantity = 0
    total = parse_amount(raw.get("total_harga") or raw.get("penjualan"))
    unit_price = parse_amount(raw.get("harga_satuan"))
    raw_total = parse_amount(raw.get("total_harga"))
    if quantity > 0 and raw_total > 0 and quantity == raw_total:
        return None, f"{product['nama']} {format_rupiah(raw_total)} — jumlah tidak disebut; sertakan kuantitas barang"
    if not total and unit_price and quantity:
        total = round(unit_price * quantity)
    if quantity <= 0 or total <= 0:
        return None, f"{product['nama']} — jumlah/harga salah"
    if quantity >= 1_000_000 or quantity >= total:
        return None, f"{product['nama']} {quantity:g} {product['satuan']} — jumlah tidak wajar atau tertukar dengan harga"
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
    if not description:
        description = f"Penj. {product['nama'].lower()} {quantity:g} {product['satuan'].lower()}"
    return {
        "produk": product["nama"],
        "kategori": product["kategori"],
        "satuan": product["satuan"],
        "kuantitas": quantity,
        "penjualan": total,
        "keterangan": description,
        "tanggal": sale_date,
        "unit": get_unit_sheet(product["nama"]),
    }, None


def _meta_sheet() -> None:
    sheet = sheet_repo.get_sheet("_Meta")
    if not sheet.get_all_values():
        sheet_repo.append_row("_Meta", ["Key", "Value"])
        sheet_repo.append_row("_Meta", ["INV_COUNTER", 0])


def _next_invoice(sale_date: date) -> str:
    _meta_sheet()
    meta = sheet_repo.get_all_records("_Meta")
    counter_row = None
    counter = 0
    for row_number, record in enumerate(meta, start=2):
        if str(record.get("Key", "")).strip() == "INV_COUNTER":
            counter_row = row_number
            counter = parse_amount(record.get("Value", 0))
            break
    if not counter:
        for unit in config.UNIT_USAHA:
            for record in sheet_repo.get_all_records(unit):
                match = re.search(r"INV/(\d+)\s*$", str(record.get("Nomor", "")))
                if match:
                    counter = max(counter, int(match.group(1)))
    if counter_row is None:
        counter_row = len(meta) + 2
    counter += 1
    sheet_repo.update_cell("_Meta", counter_row, 2, counter) if counter_row <= len(meta) + 1 else sheet_repo.append_row("_Meta", ["INV_COUNTER", counter])
    return f"{sale_date:%Y.%m.%d}/INV/{counter}"


def _save_sale(sale: dict) -> str:
    invoice = _next_invoice(sale["tanggal"])
    row = [
        invoice, sale["tanggal"].strftime("%Y-%m-%d"), sale["produk"],
        sale["keterangan"], sale["kuantitas"], sale["satuan"],
        sale["penjualan"], 0, sale["penjualan"],
    ]
    sheet_repo.append_row(sale["unit"], row)
    return invoice


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
                invoice = _save_sale(sale)
                saved.append((sale, invoice))
            except Exception as error:
                failed.append(f"{sale['produk']} — {error}")

    lines = []
    if saved:
        lines += [f"✅ *{len(saved)} TRANSAKSI BERHASIL DISIMPAN*", "━━━━━━━━━━━━━━━━━━"]
        total = 0
        for index, (sale, invoice) in enumerate(saved, start=1):
            total += sale["penjualan"]
            lines += [f"{index}. *{sale['produk']}*", f"   🧾 {invoice}",
                      f"   📅 {format_tanggal_id(datetime.combine(sale['tanggal'], datetime.min.time()))}",
                      f"   ⚖️ {sale['kuantitas']:g} {sale['satuan']} → 💰 {format_rupiah(sale['penjualan'])}"]
        lines += ["━━━━━━━━━━━━━━━━━━", f"💰 *Total: {format_rupiah(total)}*"]
    if failed:
        if saved:
            lines.append("")
        lines.append(f"⚠️ *{len(failed)} BARIS TIDAK DISIMPAN*")
        lines.extend(f"• {error}" for error in failed)
    return True, "\n".join(lines), {"invoices": [invoice for _, invoice in saved]}


def _parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _sales_rows() -> list[dict]:
    sales = []
    for unit in config.UNIT_USAHA:
        for row in sheet_repo.get_all_records(unit):
            sale_date = _parse_date(row.get("Tanggal"))
            if not sale_date:
                continue
            product = str(row.get("Nama Barang/Jasa /Produk", row.get("Produk", ""))).strip()
            if not product or re.match(r"^(sub)?total", product, re.I):
                continue
            sales.append({
                "date": sale_date,
                "product": product,
                "unit": str(row.get("Satuan (Rp)", row.get("Satuan", ""))).strip(),
                "quantity": _number(row.get("Kuantitas", 0)),
                "amount": parse_amount(row.get("Pemasukan (Rp)", row.get("Pemasukan", 0))),
            })
    return sales


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return float(parse_amount(value))


def _period_from_message(message: str) -> tuple[str, date]:
    text = message.lower().strip()
    today = now_tz().date()
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
    match = re.search(r"\b(januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember|jan|feb|mar|apr|jun|jul|agu|agt|sep|okt|nov|des)\s+(\d{4})\b", text)
    if match:
        month = MONTH_ALIASES[match.group(1)]
        return "month", date(int(match.group(2)), month, 1)
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
        "🤖 *PANDUAN BOT PENJUALAN*\n_" + config.COMPANY + "_\n━━━━━━━━━━━━━━━━━━\n\n"
        "📝 *1. CATAT PENJUALAN*\nKirim pesan seperti:\n"
        "_penj. ayam jantan 10 ekor 350rb_\n_penj. telur 100 butir 250rb_\n"
        "_penj. anggur 32 kg 960rb_\n_penj. lele 50 ekor @2000_\n\n"
        "✅ Bisa banyak transaksi dalam satu pesan, satu transaksi per baris.\n"
        "✅ Harga: `350rb`, `2,5 jt`, `1.400.000`, atau `@2000` per satuan.\n\n"
        "📊 *2. REKAP PENJUALAN*\n• `rekap` / `rekap kemarin`\n"
        "• `rekap bulan ini` / `rekap bulan lalu`\n• `rekap tahun ini` / `rekap 2026`\n"
        "• `rekap 15 okt` / `rekap 15-10-2026`\n\n"
        f"⚠️ Transaksi ≥ {format_rupiah(config.CONFIRM_THRESHOLD)} memerlukan balasan `YA` atau `BATAL`.\n"
        "👥 Ketik `help karyawan` untuk fitur karyawan dan payroll.\n━━━━━━━━━━━━━━━━━━"
    )