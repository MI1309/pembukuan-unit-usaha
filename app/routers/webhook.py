import re
import logging
import threading
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, HTTPException
from app.config import config
from app.core.whatsapp import send_whatsapp
from app.core.utils import now_tz
from app.services import sales_service
from app.services import karyawan_service, payroll_service
from app.services import backup_service
from app.services import webhook_state
from app.services.setup_service import init_all_sheets # Import fungsi di atas

router = APIRouter()


def _send_help_reply(sender: str, guide: str, copy_messages: list[str]) -> bool:
    if not send_whatsapp(sender, guide):
        return False
    if copy_messages and not send_whatsapp(sender, "📋 *PESAN SIAP DISALIN*"):
        return False
    for copy_message in copy_messages:
        if not send_whatsapp(sender, copy_message):
            return False
    return True

# setup
@router.get("/setup")
def setup_database():
    if not init_all_sheets():
        raise HTTPException(status_code=500, detail="Setup sheets gagal atau belum lengkap. Periksa log aplikasi.")
    return {"status": "success", "message": "Setup sheets selesai."}


# webhook private for Fonnte
async def _webhook_payload(request: Request) -> dict:
    try:
        value = await request.json()
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    body = (await request.body()).decode("utf-8", errors="replace")
    value = {}
    for key, items in parse_qs(body, keep_blank_values=True).items():
        if items:
            value[key] = items[-1]
    for key, item in request.query_params.items():
        if key == "secret" or key not in value:
            value[key] = item
    return value

# Tambahkan endpoint setup untuk inisialisasi sheets
def _first_value(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""

# route webhook 
@router.post("/webhook")
async def fonnte_webhook(request: Request):
    data = await _webhook_payload(request)
    return process_webhook_payload(data)


@router.get("/webhook")
def webhook_health_check():
    return {"status": "ok", "message": "Webhook endpoint aktif"}

# proses payload webhook
def process_webhook_payload(data: dict) -> dict:
    from app.core.metrics import metrics
    
    sender = ""
    message = ""
    authorized = False
    
    try:
        # 1. Rate Limit (Cheapest check, before anything else)
        raw_sender = _first_value(data, "sender", "from", "phone")
        sender = karyawan_service.normalize_phone(raw_sender)
        message = _first_value(data, "message", "text", "body", "caption")
        
        if sender and webhook_state.is_rate_limited(sender):
            metrics.increment("webhook_rate_limited", {"sender": sender})
            # Skip WA reply if heavily limited or spammy to save quota
            return {"status": "rate_limited"}

        # 2. Validasi secret
        expected_secret = str(config.WEBHOOK_SECRET or "")
        if not expected_secret or _first_value(data, "secret") != expected_secret:
            metrics.increment("webhook_unauthorized")
            raise HTTPException(status_code=401, detail="Unauthorized")

        # 3. Validasi duplikasi event (Dedupe)
        event_id = _first_value(data, "id", "message_id", "msg_id")
        if webhook_state.is_duplicate_event(event_id, message, sender):
            metrics.increment("webhook_duplicate")
            return {"status": "duplicate"}

        if not sender:
            webhook_state.log_event("", message, "INVALID_SENDER", error="sender_not_found")
            return {"status": "error", "message": "Sender tidak ditemukan."}
            
        if len(message.strip()) < 3 and not re.fullmatch(r"ya|no", message, re.I):
            return {"status": "ignored", "reason": "empty_or_short"}

        # validasi admin/staff
        staff = karyawan_service.is_admin(sender)
        backup_command = re.fullmatch(r"backup(?:\s+sekarang)?", message, re.I)
        if staff and backup_command:
            authorized = True
            # proses perintah backup manual
            result = backup_service.manual_backup()
            if result["status"] == "success":
                reply = f"✅ Backup berhasil dibuat:\n{result['name']}"
                status = "BACKUP_MANUAL_OK"
            elif result["status"] == "disabled":
                reply = "⚠️ Fitur backup sedang dinonaktifkan."
                status = "BACKUP_MANUAL_DISABLED"
            else:
                reply = "❌ Backup gagal dibuat. Periksa log aplikasi atau konfigurasi Google Drive."
                status = "BACKUP_MANUAL_FAIL"
            sent = send_whatsapp(sender, reply)
            if not sent:
                status = "BACKUP_MANUAL_REPLY_FAILED"
            webhook_state.log_event(
                sender,
                message,
                status,
                invoice=result.get("name", ""),
                error=result.get("error", "") if sent else "fonnte_send_failed",
            )
            return {"status": "backup", "result": result["status"], "reply_sent": sent}

        employee = karyawan_service.get_karyawan_by_phone(sender, active_only=False)
        if not staff and not employee:
            webhook_state.log_event(sender, message, "BLOCKED_NOT_ALLOWED", error="sender_not_allowed")
            return {"status": "blocked", "error": "sender_not_allowed"}
        authorized = True

        # Lazy trigger for periodic totals
        if getattr(config, "RECAP_LAZY_TRIGGER_ENABLED", True):
            def _safe_lazy_trigger(now, source):
                try:
                    sales_service._lazy_trigger_recap(now, source)
                except Exception as e:
                    logging.warning("lazy recap failed: %s", e)

            # Determine category for logging
            category = "transaction"
            if karyawan_service.is_help_command(message, admin=staff):
                category = "help"
            elif re.search(r"\brekap\b", message, re.I):
                category = "recap"
            elif payroll_service.handle_payroll_command(message, sender) is not None:
                category = "payroll"
            elif karyawan_service.handle_karyawan(message, sender) is not None:
                category = "karyawan"

            threading.Thread(
                target=_safe_lazy_trigger,
                args=(now_tz(), category),
                daemon=True
            ).start()

        employee_reply = karyawan_service.handle_karyawan(message, sender)
        if employee_reply is not None:
            copy_messages = (
                karyawan_service.help_copy_messages(admin=staff)
                if karyawan_service.is_help_command(message, admin=staff)
                else []
            )
            _send_help_reply(sender, employee_reply, copy_messages)
            webhook_state.log_event(sender, message, "KARYAWAN_QUERY")
            return {"status": "karyawan"}

        payroll_reply = payroll_service.handle_payroll_command(message, sender)
        if payroll_reply is not None:
            send_whatsapp(sender, payroll_reply)
            webhook_state.log_event(sender, message, "PAYROLL_QUERY")
            return {"status": "payroll"}

        if not staff:
            reply = karyawan_service.MSG_ADMIN_ONLY if backup_command else "Perintah tidak dikenali. Ketik *help karyawan*."
            send_whatsapp(sender, reply)
            webhook_state.log_event(sender, message, "ADMIN_ONLY" if backup_command else "IGNORED_NON_STAFF")
            return {"status": "ignored_non_staff"}

        help_reply = sales_service.handle_help_command(message)
        if help_reply is not None:
            sent = _send_help_reply(sender, help_reply, sales_service.help_copy_messages())
            webhook_state.log_event(
                sender,
                message,
                "HELP_QUERY" if sent else "HELP_SEND_FAILED",
                error="" if sent else "fonnte_send_failed",
            )
            return {"status": "help"}

        rekap_reply = sales_service.process_rekap(message)
        if rekap_reply is not None:
            send_whatsapp(sender, rekap_reply)
            webhook_state.log_event(sender, message, "REKAP_QUERY")
            return {"status": "rekap"}

        pending_message = webhook_state.get_pending_confirmation(sender)
        if pending_message and re.fullmatch(r"ya", message, re.I):
            webhook_state.clear_pending_confirmation(sender)
            is_sale, reply, _ = sales_service.process_sales(pending_message, confirmed=True, sender=sender)
            if is_sale:
                send_whatsapp(sender, reply)
            webhook_state.log_event(sender, "YA (konfirmasi)", "CONFIRMED")
            return {"status": "confirmed"}
        if pending_message and re.fullmatch(r"(batal|tidak|no)", message, re.I):
            webhook_state.clear_pending_confirmation(sender)
            send_whatsapp(sender, "❌ Transaksi dibatalkan.")
            webhook_state.log_event(sender, message, "CANCELLED")
            return {"status": "cancelled"}

        is_sale, reply, state = sales_service.process_sales(message, sender=sender)
        if not is_sale:
            webhook_state.log_event(sender, message, "IGNORED", error="bukan_pesan_penjualan")
            return {"status": "ignored"}
        if state and state.get("pending"):
            webhook_state.save_pending_confirmation(sender, message)
            send_whatsapp(sender, reply)
            webhook_state.log_event(sender, message, "PENDING_CONFIRM")
            return {"status": "pending_confirm"}
        send_whatsapp(sender, reply)
        invoices = ",".join((state or {}).get("invoices", []))
        webhook_state.log_event(sender, message, "SAVED", invoice=invoices)
        return {"status": "saved"}
    except HTTPException:
        raise
    except Exception as error:
        webhook_state.log_event(sender, message, "ERROR", error=str(error))
        if authorized and sender:
            send_whatsapp(sender, "❌ Sistem sedang mengalami gangguan sementara. Silakan coba lagi beberapa menit.")
        return {"status": "error", "message": "temporary_processing_error"}