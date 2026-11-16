import pytest
import json
import stat
from unittest.mock import MagicMock, patch
from app.config import config
from app.migrate_periodic_totals import main
import sys

@patch("app.services.periodic_total_service.write_period_total")
@patch("app.services.periodic_total_service.write_monthly_total")
@patch("app.services.periodic_total_service.get_period_boundaries")
@patch("app.migrate_periodic_totals._backup_sheets", return_value="backup.json")
def test_migrate_cli_apply(mock_backup, mock_boundaries, mock_write_monthly, mock_write_period):
    from app.services.periodic_total_service import Period
    from datetime import date, datetime
    
    p1 = Period("P1", date(2026, 11, 1), date(2026, 11, 7), "week")
    p_month = Period("PM", date(2026, 11, 1), date(2026, 11, 30), "month")
    mock_boundaries.return_value = [p1, p_month]
    
    # Mock sys.argv
    with patch.object(sys, 'argv', ['migrate_periodic_totals.py', '--apply', '--month', '2026-11']), \
         patch("app.migrate_periodic_totals.now_tz", return_value=datetime(2026, 11, 30, 12, 0)):
        result = main()
        
    assert result == 0
    assert mock_write_period.call_count == len(config.LEDGER_UNITS)
    assert mock_write_monthly.call_count == len(config.LEDGER_UNITS)
    assert all(call.kwargs["force"] is True for call in mock_write_period.call_args_list)
    assert all(call.kwargs["force"] is True for call in mock_write_monthly.call_args_list)
    assert all(call.args[0] != "(2)Rincian Transaksi Harian" for call in mock_write_period.call_args_list)

@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.services.periodic_total_service.write_period_total")
def test_migrate_cli_dry_run(mock_write, mock_compute):
    # Default is dry-run
    with patch.object(sys, 'argv', ['migrate_periodic_totals.py', '--month', '2026-11']):
        result = main()
        
    assert result == 0
    mock_write.assert_not_called()
    assert mock_compute.call_count == len(config.LEDGER_UNITS) * 2


@patch("app.services.periodic_total_service.write_period_total")
@patch("app.services.periodic_total_service.write_monthly_total")
@patch("app.services.periodic_total_service.get_period_boundaries")
@patch("app.migrate_periodic_totals._backup_sheets", return_value="backup.json")
def test_migrate_cli_can_target_daily_sheet(mock_backup, mock_boundaries, mock_write_monthly, mock_write_period):
    from app.services.periodic_total_service import Period
    from datetime import date, datetime

    mock_boundaries.return_value = [
        Period("P1", date(2026, 11, 1), date(2026, 11, 7), "week"),
        Period("PM", date(2026, 11, 1), date(2026, 11, 30), "month"),
    ]
    with patch.object(sys, "argv", [
        "migrate_periodic_totals.py", "--apply", "--month", "2026-11",
        "--sheet", "(2)Rincian Transaksi Harian",
    ]), patch("app.migrate_periodic_totals.now_tz", return_value=datetime(2026, 11, 30, 12, 0)):
        result = main()

    assert result == 0
    assert mock_write_period.call_count == 1
    assert mock_write_period.call_args.args[0] == "(2)Rincian Transaksi Harian"
    assert mock_write_monthly.call_count == 1
    assert mock_write_monthly.call_args.args[0] == "(2)Rincian Transaksi Harian"


def test_backfill_snapshot_stores_selected_values_with_private_permissions(tmp_path):
    from app import migrate_periodic_totals

    worksheet = MagicMock()
    worksheet.get_all_values.return_value = [["Tanggal", "Transaction ID"]]
    with patch.object(migrate_periodic_totals, "BACKUP_DIR", tmp_path), \
         patch.object(migrate_periodic_totals.sheet_repo, "get_sheet", return_value=worksheet), \
         patch.object(migrate_periodic_totals.sheet_repo, "_retry_google_call", side_effect=lambda operation, *args, **kwargs: operation(*args, **kwargs)):
        backup_path = migrate_periodic_totals._backup_sheets(["Daily"], 2026, 11)

    assert json.loads(backup_path.read_text(encoding="utf-8")) == {
        "Daily": [["Tanggal", "Transaction ID"]],
    }
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
