import re
from datetime import datetime
import pytz
from app.config import config

TZ = pytz.timezone(config.TIMEZONE)

def now_tz() -> datetime:
    return datetime.now(TZ)

def format_tanggal_id(date_obj: datetime) -> str:
    return f"{date_obj.day} {config.BULAN[date_obj.month-1]} {date_obj.year}"

def format_rupiah(value) -> str:
    return f"Rp {int(value):,}".replace(',', '.')

def parse_amount(value) -> int:
    if not value: return 0
    if isinstance(value, (int, float)): return abs(round(value))
    text = str(value).lower().strip()
    multiplier = 1
    if 'juta' in text or re.search(r'jt\b', text): multiplier = 1000000
    elif 'ribu' in text or re.search(r'rb\b', text) or re.search(r'k\b', text): multiplier = 1000
    
    text = re.sub(r'rp|juta|jt|ribu|rb|k|\s', '', text)
    if multiplier > 1:
        text = text.replace(',', '.')
        n = float(re.sub(r'[^\d.]', '', text) or 0)
        return round(n * multiplier)
    num_str = re.sub(r'[^\d]', '', text.replace('.', '').replace(',', ''))
    return int(num_str) if num_str else 0