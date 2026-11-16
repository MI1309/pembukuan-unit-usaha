import hashlib
import re
import logging
from datetime import datetime, timedelta
from threading import RLock

import pytz

from app.config import config
from app.core.utils import now_tz
from app.repositories import sheet_repo

logger = logging.getLogger(__name__)

LOG_SHEET = "_Logs"
LOG_HEADERS = ["Timestamp", "Sender", "Raw Message", "Status", "Faktur", "Error"]
PENDING_SHEET = "_PendingConfirm"
PENDING_HEADERS = ["Sender", "Message", "ExpiresAt"]
_STATE_LOCK = RLock()
_SEEN_EVENTS: dict[str, datetime] = {}
_RATE_BUCKETS: dict[str, tuple[int, int]] = {}
_PENDING_HEADERS_READY = False
_LOG_HEADERS_READY = False


def _digits(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return "62" + digits[1:] if digits.startswith("0") else digits


def _ensure_pending_sheet() -> None:
    global _PENDING_HEADERS_READY
    with _STATE_LOCK:
        if _PENDING_HEADERS_READY:
            return
        worksheet = sheet_repo.get_sheet(PENDING_SHEET)
        if not worksheet.get_all_values():
            sheet_repo.append_row(PENDING_SHEET, PENDING_HEADERS)
        _PENDING_HEADERS_READY = True


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


def is_duplicate_event(event_id: str | None, message: str = "", sender: str = "") -> bool:
    # Hash both event_id and (sender+message) for stronger dedupe
    current = now_tz()
    raw_key = f"{event_id or ''}:{sender}:{message.strip().lower()}"
    fingerprint = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    
    # TTL 60 seconds is enough for Fonnte redelivery
    ttl = 60
    
    with _STATE_LOCK:
        # Cleanup expired
        expired = [key for key, timestamp in _SEEN_EVENTS.items() if (current - timestamp).total_seconds() >= ttl]
        for key in expired:
            _SEEN_EVENTS.pop(key, None)
            
        # LRU-like limit: 1000 entries
        if len(_SEEN_EVENTS) > 1000:
            # Remove oldest
            oldest = sorted(_SEEN_EVENTS.items(), key=lambda x: x[1])[0][0]
            _SEEN_EVENTS.pop(oldest, None)

        if fingerprint in _SEEN_EVENTS:
            from app.core.metrics import track_log
            track_log("duplicate_skipped")
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


_LOG_HEADERS_READY = False
_LOG_BUFFER = []
_LAST_FLUSH = datetime.now()

def flush_logs():
    global _LOG_BUFFER, _LAST_FLUSH
    with _STATE_LOCK:
        if not _LOG_BUFFER:
            return
        try:
            sheet_repo.append_rows_atomically([(LOG_SHEET, row) for row in _LOG_BUFFER])
            _LOG_BUFFER = []
            _LAST_FLUSH = datetime.now()
        except Exception as e:
            logger.error(f"Failed to flush logs: {e}")

def log_event(sender: str, message: str, status: str, invoice: str = "", error: str = "") -> None:
    global _LOG_HEADERS_READY, _LOG_BUFFER
    try:
        # Optimasi Log: Reduksi pesan untuk status non-kritis
        full_log_statuses = getattr(config, "LOG_FULL_MESSAGE_STATUSES", {"ERROR", "PENDING_CONFIRM", "CONFIRMED"})
        msg_str = str(message or "")
        if status not in full_log_statuses:
            msg_hash = hashlib.md5(msg_str.encode()).hexdigest()[:8]
            msg_str = f"{msg_str[:50]}... [hash:{msg_hash}]"
        else:
            msg_str = msg_str[:500]

        from app.core.metrics import track_log
        track_log(status.lower())

        row = [
            now_tz().strftime("%Y-%m-%d %H:%M:%S"), str(sender or ""),
            msg_str, str(status or ""), str(invoice or ""), str(error or ""),
        ]
        
        with _STATE_LOCK:
            _LOG_BUFFER.append(row)
            # Flush if buffer >= 10 or 30 seconds passed
            if len(_LOG_BUFFER) >= 10 or (datetime.now() - _LAST_FLUSH).total_seconds() >= 30:
                flush_logs()
                
    except Exception as exception:
        logger.error(f"Webhook log error: {exception}")
