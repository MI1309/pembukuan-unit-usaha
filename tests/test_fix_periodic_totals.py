from datetime import date
from unittest.mock import MagicMock, patch

from app.fix_periodic_totals import _monthly_total_rows, _process_sheet
from app.services.periodic_total_service import Period, PeriodTotal


def test_monthly_total_rows_only_selects_requested_month():
    values = [
        ["Tanggal", "Produk", "Keterangan Transaksi", "Qty", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["2026-10-31", "", "SUBTOTAL MINGGU 4 (22-31 Oktober 2026)", "", "", "0", "0", "0", ""],
        ["2026-10-25", "SUBTOTAL MINGGU 3 Oktober 2026", "", "", "", "0", "0", "0", ""],
        ["2026-11-30", "", "TOTAL BULAN November 2026", "", "", "0", "0", "0", ""],
        ["2026-10-28", "", "", "", "SUBTOTAL MINGGU 4 Oktober 2026", "0", "0", "0", ""],
    ]

    assert _monthly_total_rows("(3)Pertanian ", values, 2026, 10) == [2, 3, 5]


def test_daily_migration_finds_legacy_totals_in_label_column():
    values = [
        ["Industri Pertanian Terpadu Lentera Farm"], [], ["Periode: Oktober 2026"], [],
        ["Tanggal", "Nama Produk", "Keterangan Transaksi", "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID"],
        ["2026-10-31", "", "", "", "[SUBTOTAL MINGGU 4 (22-31 Oktober 2026)", "0", "0", ""],
        ["2026-10-31", "", "", "", "TOTAL BULAN Oktober 2026", "0", "0", ""],
    ]

    assert _monthly_total_rows("(2)Rincian Transaksi Harian", values, 2026, 10) == [6, 7]


@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.repositories.sheet_repo.get_sheet")
def test_migration_dry_run_does_not_mutate_sheet(mock_get_sheet, mock_compute):
    from app.config import config

    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    worksheet.get_all_values.return_value = [
        ["Tanggal", "Produk", "Keterangan Transaksi", "Qty", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["2026-11-02", "Produk", "", "1", "unit", "500", "0", "500", "TX1"],
        ["2026-11-07", "", "SUBTOTAL MINGGU 1 (01-07 November 2026)", "", "", "500", "0", "500", ""],
    ]
    mock_compute.side_effect = lambda sheet, period: PeriodTotal(period, 500, 0, 500)

    result = _process_sheet(config.UNIT_USAHA[0], 2026, 11, apply=False)

    assert result["status"] == "planned"
    assert result["removed_rows"] == 1
    worksheet.insert_rows.assert_not_called()


@patch("app.services.periodic_total_service.write_monthly_total")
@patch("app.services.periodic_total_service.write_period_total")
@patch("app.services.periodic_total_service.sort_all_transactions")
@patch("app.services.periodic_total_service.normalize_row_formatting")
@patch("app.repositories.sheet_repo.delete_rows_bottom_up")
@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.repositories.sheet_repo.get_sheet")
def test_migration_apply_rebuilds_selected_month_only(
    mock_get_sheet, mock_compute, mock_delete, mock_normalize,
    mock_sort, mock_write_week, mock_write_month,
):
    from app.config import config

    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    worksheet.get_all_values.return_value = [
        ["Tanggal", "Produk", "Keterangan Transaksi", "Qty", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["2026-11-02", "Produk", "", "1", "unit", "500", "0", "500", "TX1"],
        ["2026-11-07", "", "SUBTOTAL MINGGU 1 (01-07 November 2026)", "", "", "500", "0", "500", ""],
    ]
    mock_compute.side_effect = lambda sheet, period: PeriodTotal(period, 500, 0, 500)

    result = _process_sheet(config.UNIT_USAHA[0], 2026, 11, apply=True)

    assert result["status"] == "updated"
    mock_delete.assert_called_once_with(config.UNIT_USAHA[0], [3])
    mock_sort.assert_called_once_with(config.UNIT_USAHA[0])
    assert mock_write_week.call_count == 4
    assert all(call.kwargs["force"] is True for call in mock_write_week.call_args_list)
    mock_write_month.assert_called_once_with(config.UNIT_USAHA[0], 2026, 11, force=True)
    assert mock_normalize.call_count == 2