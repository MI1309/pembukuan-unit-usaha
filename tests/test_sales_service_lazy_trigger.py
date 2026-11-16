import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, date
from app.services.sales_service import _lazy_trigger_recap, _refresh_current_period_totals
from app.config import config

@patch("app.services.periodic_total_service.process_all_units_period")
@patch("app.repositories.sheet_repo._write_archive_manifest")
@patch("app.repositories.sheet_repo._read_archive_manifest")
@patch("app.services.periodic_total_service.get_period_boundaries")
def test_lazy_trigger_recap_boundary(mock_boundaries, mock_read_meta, mock_write_meta, mock_process):
    # Setup boundary at 2026-11-07
    now = datetime(2026, 11, 7, 10, 0)
    
    from app.services.periodic_total_service import Period
    period = Period("SUBTOTAL MINGGU 1 (01-07 Nov 2026)", date(2026, 11, 1), date(2026, 11, 7), "week")
    mock_boundaries.return_value = [period]
    
    # Not yet done
    mock_read_meta.side_effect = ValueError("Not found")
    
    _lazy_trigger_recap(now, "test")
    
    mock_process.assert_called_once()
    mock_write_meta.assert_called_once()
    assert "recap_done:SUBTOTAL MINGGU 1" in mock_write_meta.call_args[0][0]

@patch("app.services.periodic_total_service.process_all_units_period")
@patch("app.repositories.sheet_repo._read_archive_manifest")
def test_lazy_trigger_recap_already_done(mock_read_meta, mock_process):
    now = datetime(2026, 11, 7, 10, 0)
    mock_read_meta.return_value = {"done": True}
    
    _lazy_trigger_recap(now, "test")
    
    mock_process.assert_not_called()

@patch("app.services.periodic_total_service.process_all_units_period")
def test_lazy_trigger_recap_disabled(mock_process):
    with patch.object(config, "RECAP_ENABLED", False):
        _lazy_trigger_recap(datetime.now(), "test")
        mock_process.assert_not_called()


@patch("app.services.sales_service.periodic_total_service.process_all_units_period", return_value={})
def test_refresh_current_totals_updates_week_and_month(mock_process):
    _refresh_current_period_totals(datetime(2026, 11, 16, 11, 0), "transaction_saved")

    assert mock_process.call_args_list[0].args == (2026, 11)
    assert mock_process.call_args_list[0].kwargs == {"period_kind": "week", "week_index": 3}
    assert mock_process.call_args_list[1].args == (2026, 11)
    assert mock_process.call_args_list[1].kwargs == {"period_kind": "month"}


@patch("app.services.periodic_total_service.process_all_units_period", return_value={})
@patch("app.repositories.sheet_repo._write_archive_manifest")
@patch("app.repositories.sheet_repo._read_archive_manifest", side_effect=ValueError("missing"))
def test_lazy_trigger_catches_up_past_boundaries(mock_read_meta, mock_write_meta, mock_process):
    _lazy_trigger_recap(datetime(2026, 11, 16, 11, 0), "transaction")

    assert mock_process.call_count == 7
    assert mock_write_meta.call_count == 7
