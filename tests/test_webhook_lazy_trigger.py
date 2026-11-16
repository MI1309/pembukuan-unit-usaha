import pytest
from unittest.mock import MagicMock, patch
from app.routers.webhook import _message_category, process_webhook_payload
from app.config import config

@patch("threading.Thread")
@patch("app.services.karyawan_service.is_admin")
@patch("app.services.webhook_state.is_duplicate_event")
@patch("app.services.webhook_state.is_rate_limited")
@patch("app.services.karyawan_service.normalize_phone")
def test_webhook_triggers_lazy_recap(mock_normalize, mock_rate, mock_dupe, mock_admin, mock_thread):
    # Setup mocks
    mock_normalize.return_value = "6285230495385"
    mock_admin.return_value = True
    mock_dupe.return_value = False
    mock_rate.return_value = False
    
    data = {
        "secret": config.WEBHOOK_SECRET,
        "sender": "6285230495385",
        "message": "rekap bulan ini",
        "id": "event123"
    }
    
    process_webhook_payload(data)
    
    # Thread should be started
    mock_thread.assert_called_once()
    assert mock_thread.call_args[1]["daemon"] is True
    mock_thread.return_value.start.assert_called_once()

@patch("threading.Thread")
@patch("app.services.karyawan_service.get_karyawan_by_phone")
@patch("app.services.karyawan_service.is_admin")
def test_webhook_no_trigger_unauthorized(mock_admin, mock_get_karyawan, mock_thread):
    mock_admin.return_value = False
    mock_get_karyawan.return_value = None # Not an employee
    
    data = {
        "secret": config.WEBHOOK_SECRET,
        "sender": "628999999",
        "message": "halo",
        "id": "event456"
    }
    
    process_webhook_payload(data)
    
    mock_thread.assert_not_called()


@patch("threading.Thread")
@patch("app.services.karyawan_service.normalize_phone", return_value="6285230495385")
@patch("app.services.webhook_state.is_rate_limited", return_value=False)
def test_webhook_no_trigger_with_invalid_secret(mock_rate, mock_normalize, mock_thread):
    from fastapi import HTTPException

    data = {"secret": "invalid", "sender": "6285230495385", "message": "rekap bulan ini"}
    with pytest.raises(HTTPException):
        process_webhook_payload(data)

    mock_thread.assert_not_called()


def test_webhook_recap_lazy_trigger_can_be_disabled():
    sender = "6285230495385"
    with patch.object(config, "RECAP_LAZY_TRIGGER_ENABLED", False), \
         patch("threading.Thread") as mock_thread, \
         patch("app.services.karyawan_service.normalize_phone", return_value=sender), \
         patch("app.services.webhook_state.is_rate_limited", return_value=False), \
         patch("app.services.webhook_state.is_duplicate_event", return_value=False), \
         patch("app.services.karyawan_service.is_admin", return_value=True), \
         patch("app.services.karyawan_service.handle_karyawan", return_value=None), \
         patch("app.services.payroll_service.handle_payroll_command", return_value=None), \
         patch("app.services.sales_service.process_rekap", return_value="rekap reply"), \
         patch("app.routers.webhook.send_whatsapp", return_value=True), \
         patch("app.services.webhook_state.log_event"):
        process_webhook_payload({
            "secret": config.WEBHOOK_SECRET,
            "sender": sender,
            "message": "rekap bulan ini",
            "id": "event-recap-disabled",
        })

    mock_thread.assert_not_called()


@patch("app.routers.webhook.payroll_service.handle_payroll_command")
@patch("app.routers.webhook.karyawan_service.handle_karyawan")
def test_category_detection_does_not_run_business_handlers(mock_karyawan, mock_payroll):
    assert _message_category("slip gaji bulan ini", staff=True) == "payroll"

    mock_payroll.assert_not_called()
    mock_karyawan.assert_not_called()


def test_webhook_categories_are_classified_from_message_text():
    assert _message_category("help", staff=True) == "help"
    assert _message_category("rekap bulan ini", staff=True) == "recap"
    assert _message_category("presensi masuk", staff=False) == "karyawan"
    assert _message_category("penj telur | warung | 10 | butir | 1500", staff=False) == "transaction"


def test_lazy_recap_exception_does_not_block_webhook_dispatch():
    from datetime import datetime
    from app.routers import webhook

    sender = "6285230495385"
    with patch.object(webhook.karyawan_service, "normalize_phone", return_value=sender), \
         patch.object(webhook.webhook_state, "is_rate_limited", return_value=False), \
         patch.object(webhook.webhook_state, "is_duplicate_event", return_value=False), \
         patch.object(webhook.karyawan_service, "is_admin", return_value=True), \
         patch.object(webhook.sales_service, "_lazy_trigger_recap", side_effect=RuntimeError("sheet unavailable")), \
         patch.object(webhook, "_start_background_recap", side_effect=lambda target, source: target(datetime(2026, 11, 16), source)), \
         patch.object(webhook.karyawan_service, "handle_karyawan", return_value=None), \
         patch.object(webhook.payroll_service, "handle_payroll_command", return_value=None), \
         patch.object(webhook.sales_service, "process_rekap", return_value="rekap reply"), \
         patch.object(webhook, "send_whatsapp", return_value=True), \
         patch.object(webhook.webhook_state, "log_event"):
        result = process_webhook_payload({
            "secret": config.WEBHOOK_SECRET,
            "sender": sender,
            "message": "rekap bulan ini",
            "id": "event-lazy-failure",
        })

    assert result["status"] == "rekap"
