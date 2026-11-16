import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, date
from app.services.sales_service import (
    _lazy_trigger_recap, _log_backdated_transaction, _refresh_current_period_totals,
)
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
    _refresh_current_period_totals(datetime(2026, 11, 30, 11, 0), "transaction_saved")

    assert mock_process.call_args_list[0].args == (2026, 11)
    assert mock_process.call_args_list[0].kwargs == {"period_kind": "week", "week_index": 4}
    assert mock_process.call_args_list[1].args == (2026, 11)
    assert mock_process.call_args_list[1].kwargs == {"period_kind": "month"}


@patch("app.services.sales_service.periodic_total_service.process_all_units_period", return_value={})
def test_refresh_current_totals_does_not_remove_partial_month_totals(mock_process):
    _refresh_current_period_totals(datetime(2026, 11, 16, 11, 0), "transaction_saved")

    mock_process.assert_not_called()


@patch("app.services.periodic_total_service.process_all_units_period", return_value={})
@patch("app.repositories.sheet_repo._write_archive_manifest")
@patch("app.repositories.sheet_repo._read_archive_manifest", side_effect=ValueError("missing"))
def test_lazy_trigger_catches_up_past_boundaries(mock_read_meta, mock_write_meta, mock_process):
    _lazy_trigger_recap(datetime(2026, 11, 16, 11, 0), "transaction")

    assert mock_process.call_count == 7
    assert mock_write_meta.call_count == 7


@patch("app.services.sales_service._process_recap_period", side_effect=RuntimeError("sheet write failed"))
@patch("app.services.periodic_total_service.get_period_boundaries")
@patch("app.repositories.sheet_repo._write_archive_manifest")
@patch("app.repositories.sheet_repo._read_archive_manifest", side_effect=ValueError("missing"))
def test_lazy_trigger_does_not_set_done_flag_after_failure(mock_read, mock_write, mock_boundaries, mock_process):
    from app.services.periodic_total_service import Period

    period = Period("SUBTOTAL MINGGU 1 (01-07 November 2026)", date(2026, 11, 1), date(2026, 11, 7), "week")
    mock_boundaries.return_value = [period]

    _lazy_trigger_recap(datetime(2026, 11, 7, 10, 0), "test")

    mock_process.assert_called_once()
    mock_write.assert_not_called()


def test_backdated_transaction_logs_manual_recap_without_sorting(caplog):
    from app.services.periodic_total_service import Period

    caplog.set_level("INFO")
    period = Period("SUBTOTAL MINGGU 1 (01-07 November 2026)", date(2026, 11, 1), date(2026, 11, 7), "week")
    with patch("app.services.sales_service.now_tz", return_value=datetime(2026, 11, 16, 10, 0)), \
         patch("app.services.sales_service.periodic_total_service.get_period_for_date", return_value=period) as get_period:
        _log_backdated_transaction(date(2026, 11, 5))

    get_period.assert_called_once_with(date(2026, 11, 5))
    assert "backdated transaction into period SUBTOTAL MINGGU 1" in caplog.text
    assert "manual recap recommended" in caplog.text


@patch("app.services.sales_service._lazy_trigger_recap")
@patch("app.services.sales_service.analyze_sales", return_value={"transaksi": []})
def test_process_sales_does_not_trigger_recap(mock_analyze, mock_lazy):
    from app.services.sales_service import process_sales

    process_sales("pesan transaksi", sender="628123456789")

    mock_lazy.assert_not_called()


@patch("app.services.sales_service.sheet_repo.get_all_records_many", return_value={"Unit": []})
def test_ledger_reader_skips_subtotals(mock_records):
    from app.services.sales_service import _sales_rows

    _sales_rows()

    assert mock_records.call_args.kwargs["skip_subtotals"] is True
