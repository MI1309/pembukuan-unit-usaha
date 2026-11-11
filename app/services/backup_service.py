from datetime import datetime, timedelta
from threading import RLock

import requests
import pytz

from app.config import config
from app.core.whatsapp import send_whatsapp
from app.core.utils import now_tz
from app.repositories import sheet_repo
from app.services.webhook_state import log_event

DRIVE_API = "https://www.googleapis.com/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_NOTIFY_LOCK = RLock()
_NOTIFIED_DATE = ""


def _drive_request(method: str, path: str, *, params=None, payload=None) -> dict:
    response = requests.request(
        method,
        f"{DRIVE_API}/{path.lstrip('/')}",
        headers={"Authorization": f"Bearer {sheet_repo.get_drive_access_token()}"},
        params=params,
        json=payload,
        timeout=30,
    )
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"Google Drive API {response.status_code}: {response.text[:500]}")
    return response.json() if response.content else {}


def _list_files(query: str, fields: str) -> list[dict]:
    files = []
    page_token = None
    while True:
        params = {
            "q": query,
            "fields": f"nextPageToken,files({fields})",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        result = _drive_request("GET", "files", params=params)
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return files


def _get_or_create_folder() -> str:
    configured_folder_id = str(getattr(config, "BACKUP_FOLDER_ID", "") or "").strip()
    if configured_folder_id:
        return configured_folder_id
    escaped_name = config.BACKUP_FOLDER_NAME.replace("'", "\\'")
    matches = _list_files(
        f"name = '{escaped_name}' and mimeType = '{_FOLDER_MIME}' and trashed = false",
        "id,name",
    )
    if matches:
        return matches[0]["id"]
    created = _drive_request("POST", "files", params={"fields": "id,name"}, payload={
        "name": config.BACKUP_FOLDER_NAME,
        "mimeType": _FOLDER_MIME,
    })
    return created["id"]


def _parse_drive_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    timezone = pytz.timezone(config.TIMEZONE)
    return timezone.localize(parsed) if parsed.tzinfo is None else parsed.astimezone(timezone)


def _prune_old_backups(folder_id: str) -> int:
    cutoff = now_tz() - timedelta(days=int(config.BACKUP_KEEP_DAYS))
    files = _list_files(f"'{folder_id}' in parents and trashed = false", "id,name,createdTime")
    deleted = 0
    for item in files:
        created = _parse_drive_time(item.get("createdTime"))
        if created and created < cutoff:
            _drive_request("DELETE", f"files/{item['id']}", params={"supportsAllDrives": "true"})
            deleted += 1
    return deleted


def _timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        timezone = pytz.timezone(config.TIMEZONE)
        return timezone.localize(value) if value.tzinfo is None else value.astimezone(timezone)
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            timezone = pytz.timezone(config.TIMEZONE)
            return timezone.localize(parsed) if parsed.tzinfo is None else parsed.astimezone(timezone)
        except ValueError:
            continue
    return None


def prune_logs(days: int = 90) -> int:
    records = sheet_repo.get_all_records("_Logs")
    cutoff = now_tz() - timedelta(days=days)
    obsolete = []
    for row_number, record in enumerate(records, start=2):
        timestamp = _timestamp(record.get("Timestamp"))
        if timestamp and timestamp < cutoff:
            obsolete.append(row_number)
    for row_number in reversed(obsolete):
        sheet_repo.delete_row("_Logs", row_number)
    return len(obsolete)


def cleanup_pending_confirmations() -> int:
    records = sheet_repo.get_all_records("_PendingConfirm")
    current = now_tz()
    expired_rows = []
    for row_number, record in enumerate(records, start=2):
        expires = _parse_drive_time(record.get("ExpiresAt"))
        if not expires or expires <= current:
            expired_rows.append(row_number)
    for row_number in expired_rows:
        sheet_repo.update_cell("_PendingConfirm", row_number, 1, "")
        sheet_repo.update_cell("_PendingConfirm", row_number, 2, "")
    return len(expired_rows)


def _notify_failure_once(message: str) -> None:
    global _NOTIFIED_DATE
    today = now_tz().strftime("%Y%m%d")
    with _NOTIFY_LOCK:
        if _NOTIFIED_DATE == today:
            return
        _NOTIFIED_DATE = today
    send_whatsapp(config.ADMIN_NOTIFY, f"🚨 Backup harian gagal: {message}")


def create_backup_copy(folder_id: str | None = None) -> dict:
    """Create and return one spreadsheet copy without pruning or cleanup."""
    folder_id = folder_id or _get_or_create_folder()
    timestamp = now_tz().strftime("%Y%m%d_%H%M%S")
    spreadsheet = sheet_repo.get_spreadsheet()
    copy_name = f"Backup_{spreadsheet.title}_{timestamp}"
    copied = _drive_request(
        "POST",
        f"files/{spreadsheet.id}/copy",
        params={"fields": "id,name,createdTime", "supportsAllDrives": "true"},
        payload={"name": copy_name, "parents": [folder_id]},
    )
    return {"id": copied.get("id"), "name": copied.get("name", copy_name)}


def manual_backup() -> dict:
    if not config.BACKUP_ENABLED:
        return {"status": "disabled"}
    try:
        copied = create_backup_copy()
        return {"status": "success", "name": copied["name"]}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def daily_backup() -> dict:
    if not config.BACKUP_ENABLED:
        return {"status": "disabled"}
    try:
        folder_id = _get_or_create_folder()
        copied = create_backup_copy(folder_id)
        deleted = _prune_old_backups(folder_id)
        logs_pruned = prune_logs(90)
        pending_pruned = cleanup_pending_confirmations()
        detail = f"log_pruned={logs_pruned};pending_pruned={pending_pruned};backups_pruned={deleted}"
        log_event("SYSTEM", "dailyBackup", "BACKUP_OK", copied["name"], detail)
        return {"status": "success", "name": copied["name"], "deleted": deleted}
    except Exception as error:
        log_event("SYSTEM", "dailyBackup", "BACKUP_FAIL", error=str(error))
        _notify_failure_once(str(error))
        return {"status": "error", "error": str(error)}
