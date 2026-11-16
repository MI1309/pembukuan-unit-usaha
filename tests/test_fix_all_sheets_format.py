from unittest.mock import MagicMock, call, patch

from app.fix_all_sheets_format import _legacy_total_rows, _process_sheet
from app.services import periodic_total_service as totals


def test_legacy_total_rows_finds_bracketed_and_monthly_labels():
    values = [
        ["Tanggal", "Nama", "Keterangan"],
        ["2026-11-07", "", "[SUBTOTAL MINGGU 1]"],
        ["2026-11-30", "", "TOTAL BULAN November 2026"],
        ["2026-11-01", "Produk", "Penjualan"],
    ]

    assert _legacy_total_rows(values, 0) == [2, 3]


@patch("app.fix_all_sheets_format._transactions_by_month")
@patch("app.fix_all_sheets_format.totals.write_monthly_total")
@patch("app.fix_all_sheets_format.totals.write_period_total")
@patch("app.fix_all_sheets_format.totals.sort_all_transactions")
@patch("app.fix_all_sheets_format.totals.normalize_sheet_rows")
@patch("app.fix_all_sheets_format.sheet_repo.delete_rows_bottom_up")
@patch("app.fix_all_sheets_format.sheet_repo.get_sheet")
def test_apply_rebuilds_only_active_weeks_and_month(
    mock_get_sheet, mock_delete, mock_normalize, mock_sort,
    mock_weekly, mock_monthly, mock_months,
):
    worksheet = MagicMock()
    worksheet.get_all_values.return_value = [
        ["Tanggal", "Nama", "Keterangan", "Qty", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["2026-11-07", "", "[SUBTOTAL MINGGU 1]", "", "", "", "", "", ""],
    ]
    mock_get_sheet.return_value = worksheet
    mock_months.side_effect = [
        {(2026, 11): {3, 10, 18, 30}},
        {(2026, 11): {3, 10, 18, 30}},
    ]

    result = _process_sheet("Unit", apply=True)

    assert result == {"status": "updated", "removed_rows": 1, "months": 1, "written_totals": 5}
    mock_delete.assert_called_once_with("Unit", [2])
    assert mock_normalize.call_args == call("Unit")
    assert mock_sort.call_args == call("Unit")
    assert [entry.args[1].label for entry in mock_weekly.call_args_list] == [
        totals.get_period_boundaries(2026, 11)[index].label for index in range(4)
    ]
    assert all(entry.kwargs == {"force": True} for entry in mock_weekly.call_args_list)
    mock_monthly.assert_called_once_with("Unit", 2026, 11, force=True)