import re
from datetime import datetime
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import pytz
from app.config import config

# beberapa fungsi utilitas yang digunakan di berbagai bagian aplikasi, terutama untuk menangani tanggal, format mata uang, dan parsing jumlah.

TZ = pytz.timezone(config.TIMEZONE)

def now_tz() -> datetime:
    return datetime.now(TZ)

def format_tanggal_id(date_obj: datetime) -> str:
    return f"{date_obj.day} {config.BULAN[date_obj.month-1]} {date_obj.year}"

def format_rupiah(value) -> str:
    return f"Rp {int(value):,}".replace(',', '.')

def parse_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None

    for pattern in (
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
        "%Y.%m.%d", "%d %b %Y", "%d %B %Y", "%d %b, %Y",
    ):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue

    month_names = {
        "januari": "January", "februari": "February", "maret": "March",
        "april": "April", "mei": "May", "juni": "June", "juli": "July",
        "agustus": "August", "september": "September", "oktober": "October",
        "november": "November", "desember": "December",
    }
    normalized = text.casefold()
    for local_name, english_name in month_names.items():
        normalized = re.sub(rf"\b{local_name}\b", english_name, normalized)
    for pattern in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(normalized.title(), pattern).date()
        except ValueError:
            continue
    return None

def parse_amount(value) -> int:
    if value is None or value == "": return 0
    if isinstance(value, (int, float)):
        return int(abs(Decimal(str(value))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = str(value).lower().strip()
    multiplier = 1
    if 'juta' in text or re.search(r'jt\b', text): multiplier = 1000000
    elif 'ribu' in text or re.search(r'rb\b', text) or re.search(r'k\b', text): multiplier = 1000
    
    text = re.sub(r'rp|juta|jt|ribu|rb|k|\s', '', text)
    if multiplier > 1:
        text = re.sub(r'[^\d,.+-]', '', text)
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        n = float(text or 0)
        return int(abs(Decimal(str(n * multiplier))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = re.sub(r'[^\d,.+-]', '', text)
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        text = text.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts) if len(parts[-1]) == 3 else ".".join(parts)
    elif "." in text:
        parts = text.split(".")
        text = "".join(parts) if len(parts[-1]) == 3 else text
    try:
        return int(abs(Decimal(text)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if text else 0
    except ValueError:
        return 0