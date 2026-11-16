import re
import time
import random
import logging
from threading import Lock

import requests

from app.config import config
from app.core.utils import now_tz
from app.core.metrics import track_fonnte

logger = logging.getLogger(__name__)

_NOTIFY_LOCK = Lock()
_NOTIFIED_DATE = ""

# whatsapp.py digunakan untuk mengirim pesan WhatsApp melalui Fonnte API, termasuk penanganan kesalahan, retry, dan notifikasi ke admin jika terjadi masalah dengan kuota atau batasan API.


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
    
    track_fonnte("attempt")
    last_error = None
    # Optimasi: Jitter + Max 2 retry (total 3 attempts)
    for attempt in range(3):
        try:
            response = requests.post(
                config.FONNTE_URL,
                headers={"Authorization": config.FONNTE_TOKEN},
                data={"target": target, "message": message},
                timeout=15,
            )
            body = response.text or ""
            
            if (response.status_code >= 500 or response.status_code == 429) and attempt < 2:
                last_error = RuntimeError(f"Fonnte HTTP {response.status_code}")
                # Exponential backoff with jitter
                sleep_time = (2 ** attempt) + (random.random() * 1)
                time.sleep(sleep_time)
                track_fonnte("retry")
                continue
                
            if response.status_code < 200 or response.status_code >= 300:
                logger.error(f"Fonnte error {response.status_code}: {body[:300]}")
                if re.search(r"quota|limit|insufficient|saldo", body, re.I):
                    _notify_admin_once(f"Fonnte kuota habis: {body[:200]}")
                track_fonnte("failed_http")
                return False
                
            try:
                result = response.json()
            except ValueError:
                result = {}
                
            if isinstance(result, dict) and result.get("status") is False:
                detail = str(result.get("reason") or result.get("message") or body)
                if re.search(r"quota|limit|insufficient|saldo", detail, re.I):
                    _notify_admin_once(f"Fonnte kuota habis: {detail[:200]}")
                logger.error(f"Fonnte rejected message: {detail[:300]}")
                track_fonnte("rejected")
                return False
                
            track_fonnte("sent")
            return True

        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            if attempt < 2:
                sleep_time = (2 ** attempt) + (random.random() * 1)
                time.sleep(sleep_time)
                track_fonnte("retry")
                continue
            break
        except requests.RequestException as error:
            last_error = error
            break
            
    logger.error(f"Fonnte gagal setelah retry: {last_error}")
    track_fonnte("failed_final")
    return False