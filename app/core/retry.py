import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import wraps

# retry.py digunakan untuk menangani retry pada panggilan API Google, terutama untuk status kode HTTP yang bersifat sementara (transient), seperti 429 (Too Many Requests) atau 500 (Internal Server Error).

# dengan tujuan untuk meningkatkan keandalan aplikasi saat berinteraksi dengan layanan Google, terutama ketika terjadi kesalahan sementara yang dapat diatasi dengan mencoba kembali operasi tersebut setelah beberapa waktu.

logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _response_from_error(error):
    response = getattr(error, "response", None)
    if response is None:
        response = getattr(error, "resp", None)
    return response


def error_code(error):
    response = _response_from_error(error)
    for value in (
        getattr(response, "status_code", None),
        getattr(response, "status", None),
        getattr(error, "status_code", None),
        getattr(error, "code", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _retry_after_seconds(error):
    response = _response_from_error(error)
    headers = getattr(response, "headers", None) or {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _is_retryable_google_error(error):
    return error_code(error) in RETRYABLE_STATUS_CODES


def _is_transient_connection_error(error):
    try:
        import requests
    except ImportError:
        requests = None
    transient_types = [TimeoutError, ConnectionError]
    if requests is not None:
        transient_types.extend((requests.exceptions.Timeout, requests.exceptions.ConnectionError))
    return isinstance(error, tuple(transient_types))


def _retry_delay(error, attempt, base_delay, max_delay, jitter):
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return min(max_delay, retry_after)
    delay = min(max_delay, base_delay * (2 ** attempt))
    return delay * (0.5 + random.random()) if jitter else delay


def retry_google_api(max_attempts=5, base_delay=1.0, max_delay=30.0, jitter=True):
    """Retry Google API calls for transient HTTP status codes."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return function(*args, **kwargs)
                except Exception as error:
                    if attempt + 1 >= max_attempts or not _is_retryable_google_error(error):
                        raise
                    delay = _retry_delay(error, attempt, base_delay, max_delay, jitter)
                    logger.warning(
                        "Google API retry operation=%s attempt=%s delay=%.3f error_code=%s",
                        getattr(function, "__qualname__", function.__class__.__name__),
                        attempt + 1, delay, error_code(error),
                    )
                    time.sleep(delay)
            raise RuntimeError("Google API retry loop exited unexpectedly")

        return wrapped

    return decorate


def safe_append_with_idempotency(
    operation,
    transaction_ids,
    find_existing,
    *,
    max_attempts=5,
    base_delay=1.0,
    max_delay=30.0,
    jitter=True,
    operation_name="append",
):
    """Retry an append only after confirming none of its IDs were committed."""
    transaction_ids = set(transaction_ids or [])
    for attempt in range(max_attempts):
        try:
            return operation()
        except Exception as error:
            retryable = _is_retryable_google_error(error) or _is_transient_connection_error(error)
            if attempt + 1 >= max_attempts or not retryable or not transaction_ids or not find_existing:
                raise

            existing = set(find_existing(sorted(transaction_ids)) or [])
            if transaction_ids.issubset(existing):
                logger.warning(
                    "Google API append confirmed after uncertain response operation=%s error_code=%s",
                    operation_name, error_code(error),
                )
                return None
            if existing:
                raise

            delay = _retry_delay(error, attempt, base_delay, max_delay, jitter)
            logger.warning(
                "Google API retry operation=%s attempt=%s delay=%.3f error_code=%s",
                operation_name, attempt + 1, delay, error_code(error),
            )
            time.sleep(delay)
    raise RuntimeError("Google API append retry loop exited unexpectedly")