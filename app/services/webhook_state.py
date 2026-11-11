import hashlib
import re
from datetime import datetime, timedelta
from threading import RLock

import pytz

from app.config import config
from app.core.utils import now_tz
from app.repositories import sheet_repo

LOG_SHEET = "_Logs"
LOG_HEADERS = ["Timestamp", "Sender", "Raw Message", "Status", "Faktur", "Error"]
PENDING_SHEET = "_PendingConfirm"
PENDING_HEADERS = ["Sender", "Message", "ExpiresAt"]
_STATE_LOCK = RLock()
_SEEN_EVENTS: dict[str, datetime] = {}
_RATE_BUCKETS: dict[str, tuple[int, int]] = {}


def _digits(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return "62" + digits[1:] if digits.startswith("0") else digits


def _ensure_pending_sheet() -> None:
    worksheet = sheet_repo.get_sheet(PENDING_SHEET)
    if not worksheet.get_all_values():
        sheet_repo.append_row(PENDING_SHEET, PENDING_HEADERS)


def _aware_datetime(value) -> datetime | None:
    timezone = pytz.timezone(config.TIMEZONE)
    if isinstance(value, datetime):
        return timezone.localize(value) if value.tzinfo is None else value.astimezone(timezone)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timezone.localize(parsed) if parsed.tzinfo is None else parsed.astimezone(timezone)


def is_duplicate_event(event_id: str | None) -> bool:
    if not event_id:
        return False
    current = now_tz()
    fingerprint = hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()
    ttl = int(getattr(config, "WEBHOOK_DEDUPE_TTL_SECONDS", 180))
    with _STATE_LOCK:
        expired = [key for key, timestamp in _SEEN_EVENTS.items() if (current - timestamp).total_seconds() >= ttl]
        for key in expired:
            _SEEN_EVENTS.pop(key, None)
        if fingerprint in _SEEN_EVENTS:
            return True
        _SEEN_EVENTS[fingerprint] = current
        return False


def is_rate_limited(sender: str) -> bool:
    limit = int(getattr(config, "RATE_LIMIT_PER_MINUTE", 10))
    if limit <= 0:
        return False
    current = now_tz()
    key = _digits(sender)
    minute_bucket = int(current.timestamp() // 60)
    with _STATE_LOCK:
        previous_bucket, count = _RATE_BUCKETS.get(key, (minute_bucket, 0))
        if previous_bucket != minute_bucket:
            count = 0
        if count >= limit:
            _RATE_BUCKETS[key] = (minute_bucket, count)
            return True
        _RATE_BUCKETS[key] = (minute_bucket, count + 1)
        return False


def save_pending_confirmation(sender: str, message: str) -> None:
    _ensure_pending_sheet()
    expires = now_tz() + timedelta(seconds=int(getattr(config, "CONFIRM_TTL_SECONDS", 300)))
    with _STATE_LOCK:
        clear_pending_confirmation(sender)
        sheet_repo.append_row(PENDING_SHEET, [_digits(sender), message, expires.isoformat()])


def get_pending_confirmation(sender: str) -> str | None:
    _ensure_pending_sheet()
    target = _digits(sender)
    current = now_tz()
    records = sheet_repo.get_all_records(PENDING_SHEET)
    for row_number, record in enumerate(records, start=2):
        if _digits(record.get("Sender", "")) != target:
            continue
        expires = _aware_datetime(record.get("ExpiresAt"))
        if not expires or expires <= current:
            with _STATE_LOCK:
                sheet_repo.update_cell(PENDING_SHEET, row_number, 1, "")
                sheet_repo.update_cell(PENDING_SHEET, row_number, 2, "")
            continue
        return str(record.get("Message", "")) or None
    return None


def clear_pending_confirmation(sender: str) -> None:
    _ensure_pending_sheet()
    target = _digits(sender)
    for row_number, record in enumerate(sheet_repo.get_all_records(PENDING_SHEET), start=2):
        if _digits(record.get("Sender", "")) == target:
            sheet_repo.update_cell(PENDING_SHEET, row_number, 1, "")
            sheet_repo.update_cell(PENDING_SHEET, row_number, 2, "")


def log_event(sender: str, message: str, status: str, invoice: str = "", error: str = "") -> None:
    try:
        worksheet = sheet_repo.get_sheet(LOG_SHEET)
        values = worksheet.get_all_values()
        if not values:
            sheet_repo.append_row(LOG_SHEET, LOG_HEADERS)
        elif len(values[0]) < len(LOG_HEADERS) or values[0][:len(LOG_HEADERS)] != LOG_HEADERS:
            worksheet.update("A1:F1", [LOG_HEADERS])
        sheet_repo.append_row(LOG_SHEET, [
            now_tz().strftime("%Y-%m-%d %H:%M:%S"), str(sender or ""),
            str(message or "")[:500], str(status or ""), str(invoice or ""), str(error or ""),
        ])
    except Exception as exception:
        print(f"Webhook log error: {exception}")
