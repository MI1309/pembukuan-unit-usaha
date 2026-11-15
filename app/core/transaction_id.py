import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# id transaksi dibangun dari beberapa field bisnis yang dinormalisasi, sehingga menghasilkan ID yang stabil dan unik untuk setiap transaksi.

_MONTHS_ID = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5,
    "juni": 6, "juli": 7, "agustus": 8, "agu": 8, "agt": 8,
    "september": 9, "oktober": 10, "okt": 10, "november": 11,
    "nov": 11, "desember": 12, "des": 12,
}


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
}


def _transaction_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y.%m.%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    localized = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if localized and localized.group(2).lower() in _MONTHS_ID:
        try:
            return date(
                int(localized.group(3)),
                _MONTHS_ID[localized.group(2).lower()],
                int(localized.group(1)),
            )
        except ValueError:
            pass
    raise ValueError(f"Tanggal transaksi tidak valid: {text}")


def _canonical_quantity(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    numeric = re.fullmatch(r"([+-]?\d+(?:[.,]\d+)?)\s*(.*)", text)
    if numeric:
        try:
            number = float(numeric.group(1).replace(",", "."))
            amount = f"{number:g}"
        except ValueError:
            amount = numeric.group(1)
        return f"{amount} {numeric.group(2)}".strip()
    return text


def _unit_code(unit) -> str:
    value = re.sub(r"\s+", " ", str(unit or "").strip().lower())
    if re.fullmatch(r"[A-Z]{3}", str(unit or "")):
        return str(unit).upper()
    return UNIT_CODES.get(value, "UNK")


def generate_transaction_id(sender, tanggal, produk, kuantitas, nominal, keterangan, unit) -> str:
    """Build a stable transaction ID from its normalized business fields."""
    transaction_date = _transaction_date(tanggal)
    normalized_date = transaction_date.isoformat()
    normalized_sender = re.sub(r"\D", "", str(sender or ""))
    normalized_product = re.sub(r"\s+", " ", str(produk or "").strip()).casefold()
    normalized_quantity = _canonical_quantity(kuantitas)
    try:
        normalized_amount = str(int(Decimal(str(nominal).replace(",", "."))))
    except (TypeError, ValueError, InvalidOperation):
        normalized_amount = re.sub(r"\D", "", str(nominal or "")) or "0"
    normalized_description = re.sub(r"\s+", " ", str(keterangan or "").strip()).casefold()
    payload = "|".join((
        normalized_sender,
        normalized_date,
        normalized_product,
        normalized_quantity,
        normalized_amount,
        normalized_description,
    ))
    hash8 = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"{transaction_date:%Y%m%d}-{_unit_code(unit)}-{hash8}"