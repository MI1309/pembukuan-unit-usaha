import re
import time
from threading import Lock

import requests

from app.config import config
from app.core.utils import now_tz

_NOTIFY_LOCK = Lock()
_NOTIFIED_DATE = ""


def _notify_admin_once(message: str) -> None:
    global _NOTIFIED_DATE
    today = now_tz().strftime("%Y%m%d")
    with _NOTIFY_LOCK:
        if _NOTIFIED_DATE == today:
            return
        _NOTIFIED_DATE = today
    if not config.FONNTE_TOKEN or not config.ADMIN_NOTIFY:
        return
    try:
        requests.post(
            config.FONNTE_URL,
            headers={"Authorization": config.FONNTE_TOKEN},
            data={"target": config.ADMIN_NOTIFY, "message": f"🚨 {message}"},
            timeout=10,
        )
    except requests.RequestException as error:
        print(f"Fonnte admin notification error: {error}")


def send_whatsapp(target: str, message: str) -> bool:
    if not config.FONNTE_TOKEN:
        return False
    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(
                config.FONNTE_URL,
                headers={"Authorization": config.FONNTE_TOKEN},
                data={"target": target, "message": message},
                timeout=10,
            )
            body = response.text or ""
            if (response.status_code >= 500 or response.status_code == 429) and attempt == 0:
                last_error = RuntimeError(f"Fonnte HTTP {response.status_code}")
                time.sleep(1)
                continue
            if response.status_code < 200 or response.status_code >= 300:
                print(f"Fonnte error {response.status_code}: {body[:300]}")
                if re.search(r"quota|limit|insufficient|saldo", body, re.I):
                    _notify_admin_once(f"Fonnte kuota habis: {body[:200]}")
                return False
            try:
                result = response.json()
            except ValueError:
                result = {}
            if isinstance(result, dict) and result.get("status") is False:
                detail = str(result.get("reason") or result.get("message") or body)
                if re.search(r"quota|limit|insufficient|saldo", detail, re.I):
                    _notify_admin_once(f"Fonnte kuota habis: {detail[:200]}")
                print(f"Fonnte rejected message: {detail[:300]}")
                return False
            return True
        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(1)
                continue
            break
        except requests.RequestException as error:
            last_error = error
            break
    print(f"Fonnte gagal setelah retry: {last_error}")
    return False