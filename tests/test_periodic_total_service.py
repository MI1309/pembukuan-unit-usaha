import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from app.services.periodic_total_service import (
    Period, compute_period_total, is_subtotal_row, ensure_sorted
)

def test_is_subtotal_row():
    assert is_subtotal_row(["", "", "SUBTOTAL MINGGU 1 (01-07 Jan 2026)"]) is True
    assert is_subtotal_row(["", "", "TOTAL BULAN Januari 2026"]) is True
    assert is_subtotal_row(["", "", "Jual Telur"]) is False
    assert is_subtotal_row([]) is False

@patch("app.repositories.sheet_repo.get_sheet")
def test_compute_period_total(mock_get_sheet):
    mock_worksheet = MagicMock()
    mock_get_sheet.return_value = mock_worksheet
    
    # Mock data rows (0: tgl, 1: produk, 2: ket, 3: qty, 4: sat, 5: inc, 6: exp, 7: bal, 8: txid)
    mock_worksheet.get_all_values.return_value = [
        ["2026-01-01", "A", "Ket A", "1", "100", "100", "0", "100", "TX1"],
        ["2026-01-05", "B", "Ket B", "2", "50", "100", "0", "200", "TX2"],
        ["2026-01-10", "C", "Ket C", "1", "50", "50", "0", "250", "TX3"], # Outside period
    ]
    
    period = Period("SUBTOTAL MINGGU 1", date(2026, 1, 1), date(2026, 1, 7), "week")
    total = compute_period_total("Unit A", period)
    
    assert total.pemasukan == 200
    assert total.pengeluaran == 0
    assert total.sisa_saldo_akhir == 200

@patch("app.repositories.sheet_repo.rewrite_period_block")
@patch("app.repositories.sheet_repo._detect_header_row_index")
@patch("app.repositories.sheet_repo.get_sheet")
def test_ensure_sorted(mock_get_sheet, mock_detect_header, mock_rewrite):
    mock_worksheet = MagicMock()
    mock_get_sheet.return_value = mock_worksheet
    mock_detect_header.return_value = 0 # Header at row 0
    
    # Unsorted rows
    rows = [
        ["2026-01-05", "B", "Ket B", "1", "100", "100", "0", "100", "TX2"],
        ["2026-01-01", "A", "Ket A", "1", "100", "100", "0", "200", "TX1"],
    ]
    mock_worksheet.get_all_values.return_value = [
        ["Tanggal", "Produk", "Keterangan", "Qty", "Sat", "Inc", "Exp", "Bal", "Transaction ID"],
        *rows
    ]
    
    period = Period("SUBTOTAL MINGGU 1", date(2026, 1, 1), date(2026, 1, 7), "week")
    
    changed = ensure_sorted("Unit A", period)
    
    assert changed is True
    mock_rewrite.assert_called_once()
    args = mock_rewrite.call_args[0]
    # args[3] is new_rows, should be sorted
    assert args[3][0][0] == "2026-01-01"
    assert args[3][1][0] == "2026-01-05"
