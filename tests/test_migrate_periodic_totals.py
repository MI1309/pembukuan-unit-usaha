import pytest
from unittest.mock import MagicMock, patch
from app.migrate_periodic_totals import main
import sys

@patch("app.services.periodic_total_service.write_period_total")
@patch("app.services.periodic_total_service.write_monthly_total")
@patch("app.services.periodic_total_service.get_period_boundaries")
def test_migrate_cli_apply(mock_boundaries, mock_write_monthly, mock_write_period):
    from app.services.periodic_total_service import Period
    from datetime import date
    
    p1 = Period("P1", date(2026, 11, 1), date(2026, 11, 7), "week")
    p_month = Period("PM", date(2026, 11, 1), date(2026, 11, 30), "month")
    mock_boundaries.return_value = [p1, p_month]
    
    # Mock sys.argv
    with patch.object(sys, 'argv', ['migrate_periodic_totals.py', '--apply', '--month', '2026-11']):
        main()
        
    mock_write_period.assert_called()
    mock_write_monthly.assert_called()

@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.services.periodic_total_service.write_period_total")
def test_migrate_cli_dry_run(mock_write, mock_compute):
    # Default is dry-run
    with patch.object(sys, 'argv', ['migrate_periodic_totals.py', '--month', '2026-11']):
        main()
        
    mock_write.assert_not_called()
    mock_compute.assert_called()
