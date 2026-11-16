import pytest
from unittest.mock import MagicMock, patch
from app.routers.webhook import process_webhook_payload
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
