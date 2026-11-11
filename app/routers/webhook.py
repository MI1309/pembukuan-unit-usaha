import re
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, HTTPException
from app.config import config
from app.core.whatsapp import send_whatsapp
from app.services import sales_service
from app.services import karyawan_service, payroll_service
from app.services import webhook_state
from app.services.setup_service import init_all_sheets # Import fungsi di atas

router = APIRouter()

@router.get("/setup")
def setup_database():
    init_all_sheets()
    return {"status": "success", "message": "Setup sheets selesai. Cek terminal untuk detail."}


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
        value.setdefault(key, item)
    return value


def _first_value(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


@router.post("/webhook")
async def fonnte_webhook(request: Request):
    data = await _webhook_payload(request)
    return process_webhook_payload(data)


@router.get("/webhook")
def webhook_health_check():
    return {"status": "ok", "message": "Webhook endpoint aktif"}


def process_webhook_payload(data: dict) -> dict:
    sender = ""
    message = ""
    try:
        expected_secret = str(config.WEBHOOK_SECRET or "")
        if not expected_secret or _first_value(data, "secret") != expected_secret:
            raise HTTPException(status_code=401, detail="Unauthorized")

        event_id = _first_value(data, "id", "message_id", "msg_id")
        if webhook_state.is_duplicate_event(event_id):
            return {"status": "duplicate"}

        raw_sender = _first_value(data, "sender", "from", "phone")
        sender = karyawan_service.normalize_phone(raw_sender)
        message = _first_value(data, "message", "text", "body", "caption")
        if not sender:
            webhook_state.log_event("", message, "INVALID_SENDER", error="sender_not_found")
            return {"status": "error", "message": "Sender tidak ditemukan."}
        if len(message) < 3 and not re.fullmatch(r"ya|no", message, re.I):
            return {"status": "ignored", "reason": "empty_or_short"}

        staff = karyawan_service.is_admin(sender)
        employee = karyawan_service.get_karyawan_by_phone(sender, active_only=False)
        if not staff and not employee:
            webhook_state.log_event(sender, message, "BLOCKED_NOT_ALLOWED", error="sender_not_allowed")
            return {"status": "blocked", "error": "sender_not_allowed"}

        if webhook_state.is_rate_limited(sender):
            webhook_state.log_event(sender, message, "RATE_LIMITED", error="too_many_requests")
            send_whatsapp(sender, "⚠️ Terlalu banyak pesan. Coba lagi dalam 1 menit.")
            return {"status": "rate_limited"}

        employee_reply = karyawan_service.handle_karyawan(message, sender)
        if employee_reply is not None:
            send_whatsapp(sender, employee_reply)
            webhook_state.log_event(sender, message, "KARYAWAN_QUERY")
            return {"status": "karyawan"}

        payroll_reply = payroll_service.handle_payroll_command(message, sender)
        if payroll_reply is not None:
            send_whatsapp(sender, payroll_reply)
            webhook_state.log_event(sender, message, "PAYROLL_QUERY")
            return {"status": "payroll"}

        if not staff:
            send_whatsapp(sender, "Perintah tidak dikenali. Ketik *help karyawan*.")
            webhook_state.log_event(sender, message, "IGNORED_NON_STAFF")
            return {"status": "ignored_non_staff"}

        help_reply = sales_service.handle_help_command(message)
        if help_reply is not None:
            sent = send_whatsapp(sender, help_reply)
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
            is_sale, reply, _ = sales_service.process_sales(pending_message, confirmed=True)
            if is_sale:
                send_whatsapp(sender, reply)
            webhook_state.log_event(sender, "YA (konfirmasi)", "CONFIRMED")
            return {"status": "confirmed"}
        if pending_message and re.fullmatch(r"(batal|tidak|no)", message, re.I):
            webhook_state.clear_pending_confirmation(sender)
            send_whatsapp(sender, "❌ Transaksi dibatalkan.")
            webhook_state.log_event(sender, message, "CANCELLED")
            return {"status": "cancelled"}

        is_sale, reply, state = sales_service.process_sales(message)
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
        employee = karyawan_service.get_karyawan_by_phone(sender, active_only=False) if sender else None
        if sender and (karyawan_service.is_admin(sender) or employee):
            send_whatsapp(sender, f"❌ Gagal memproses pesan: {error}\nSilakan kirim ulang.")
        return {"status": "error", "error": str(error)}