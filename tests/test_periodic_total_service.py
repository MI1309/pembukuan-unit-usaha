import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from app.services.periodic_total_service import (
    Period, PeriodTotal, compute_period_total, ensure_sorted,
    write_period_total, write_monthly_total, is_subtotal_row,
)
from app.core.utils import parse_amount, parse_date

def test_is_subtotal_row():
    # idx 2 is Keterangan
    assert is_subtotal_row(["2026-01-01", "", "SUBTOTAL MINGGU 1 (01-07 Jan 2026)"]) is True
    assert is_subtotal_row(["2026-01-31", "", "TOTAL BULAN Januari 2026"]) is True
    assert is_subtotal_row(["2026-01-01", "Telur", "Jual Telur"]) is False
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

def test_robust_date_and_amount_parsing():
    assert parse_date("2026-10-31") == date(2026, 10, 31)
    assert parse_date("31 Oct 2026") == date(2026, 10, 31)
    assert parse_date("31 Oktober 2026") == date(2026, 10, 31)
    assert parse_amount("Rp 1.234.500") == 1_234_500
    assert parse_amount("Rp 1.234,50") == 1_235

@patch("app.repositories.sheet_repo.get_sheet")
def test_compute_period_total_uses_ledger_headers_and_skips_subtotals(mock_get_sheet):
    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    worksheet.get_all_values.return_value = [
        ["Tanggal", "Nama Barang/Jasa /Produk", "Keterangan Transaksi", "Kuantitas", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["31 Oct 2026", "Kohe Kambing", "Pembelian", "1", "karung", "Rp 1.250.000", "Rp 0", "Rp 1.250.000", "TX1"],
        ["2026-10-31", "Herbisida", "Penjualan", "1", "botol", "Rp 250.000", "Rp 50.000", "Rp 1.450.000", "TX2"],
        ["2026-10-31", "", "SUBTOTAL MINGGU 4 (22-31 Oktober 2026)", "", "", "Rp 1.500.000", "Rp 50.000", "Rp 1.450.000", ""],
    ]
    period = Period("SUBTOTAL MINGGU 4", date(2026, 10, 22), date(2026, 10, 31), "week")

    total = compute_period_total("(3)Pertanian ", period)

    assert (total.pemasukan, total.pengeluaran, total.sisa_saldo_akhir) == (1_500_000, 50_000, 1_450_000)

@patch("app.repositories.sheet_repo.insert_rows_after")
@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.repositories.sheet_repo.get_sheet")
@patch("app.services.periodic_total_service.ensure_sorted")
def test_weekly_subtotal_is_inserted_after_last_physical_transaction(
    mock_sorted, mock_get_sheet, mock_compute, mock_insert,
):
    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    worksheet.get_all_values.return_value = [
        ["Tanggal", "Produk", "Keterangan", "Qty", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["2026-10-02", "A", "", "1", "kg", "100", "0", "100", "TX1"],
        [],
        ["2026-10-06", "B", "", "1", "kg", "200", "0", "300", "TX2"],
        ["2026-10-08", "C", "", "1", "kg", "300", "0", "600", "TX3"],
    ]
    period = Period("SUBTOTAL MINGGU 1 (01-07 Oktober 2026)", date(2026, 10, 1), date(2026, 10, 7), "week")
    mock_compute.return_value = PeriodTotal(period, 300, 0, 300)

    write_period_total("(3)Pertanian ", period)

    mock_insert.assert_called_once()
    sheet_name, after_row, inserted_rows = mock_insert.call_args.args
    assert sheet_name == "(3)Pertanian "
    assert after_row == 4
    assert inserted_rows[0] == ["2026-10-07", "", period.label, "", "", 300, 0, 300, ""]

@patch("app.repositories.sheet_repo.insert_rows_after")
@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.repositories.sheet_repo.get_sheet")
@patch("app.services.periodic_total_service.ensure_sorted")
def test_monthly_total_is_inserted_after_week_four_subtotal(
    mock_sorted, mock_get_sheet, mock_compute, mock_insert,
):
    from app.services.periodic_total_service import get_period_boundaries

    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    periods = get_period_boundaries(2026, 10)
    week4 = periods[3]
    monthly = periods[4]
    worksheet.get_all_values.return_value = [
        ["Tanggal", "Produk", "Keterangan", "Qty", "Satuan", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Sisa Saldo", "Transaction ID"],
        ["2026-10-31", "Neo Gosong", "Penjualan", "1", "kg", "500", "0", "500", "TX1"],
        ["2026-10-31", "", week4.label, "", "", "500", "0", "500", ""],
        ["2026-11-01", "Barang November", "", "1", "kg", "700", "0", "1200", "TX2"],
    ]
    mock_compute.return_value = PeriodTotal(monthly, 500, 0, 500)

    write_monthly_total("(3)Pertanian ", 2026, 10)

    _, after_row, inserted_rows = mock_insert.call_args.args
    assert after_row == 3
    assert inserted_rows[0] == ["2026-10-31", "", monthly.label, "", "", 500, 0, 500, ""]

@patch("app.repositories.sheet_repo.get_sheet")
def test_daily_detail_total_respects_header_row_five(mock_get_sheet):
    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    worksheet.get_all_values.return_value = [
        ["Industri Pertanian Terpadu Lentera Farm"],
        [],
        ["Periode: Oktober 2026"],
        [],
        ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID"],
        ["31 Oct 2026", "Herbisida", "Penjualan", "1", "botol", "Rp 1.000", "Rp 0", "TX1"],
        ["2026-10-31", "Neo Gosong", "Pembelian", "1", "unit", "Rp 0", "Rp 250", "TX2"],
    ]
    period = Period("SUBTOTAL MINGGU 4", date(2026, 10, 22), date(2026, 10, 31), "week")

    total = compute_period_total("(2)Rincian Transaksi Harian", period)

    assert (total.pemasukan, total.pengeluaran, total.sisa_saldo_akhir) == (1_000, 250, 0)

@patch("app.repositories.sheet_repo.insert_rows_after")
@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.repositories.sheet_repo.get_sheet")
@patch("app.services.periodic_total_service.ensure_sorted")
def test_daily_subtotal_is_inserted_after_last_data_row(
    mock_sorted, mock_get_sheet, mock_compute, mock_insert,
):
    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    worksheet.get_all_values.return_value = [
        ["Industri Pertanian Terpadu Lentera Farm"], [], ["Periode: Oktober 2026"], [],
        ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID"],
        ["2026-10-24", "Produk A", "", "1", "unit", "500", "0", "TX1"],
        [],
        ["2026-10-30", "Produk B", "", "1", "unit", "700", "0", "TX2"],
    ]
    period = Period("SUBTOTAL MINGGU 4 (22-31 Oktober 2026)", date(2026, 10, 22), date(2026, 10, 31), "week")
    mock_compute.return_value = PeriodTotal(period, 1200, 0, 0)

    write_period_total("(2)Rincian Transaksi Harian", period)

    _, after_row, inserted_rows = mock_insert.call_args.args
    assert after_row == 8
    assert len(inserted_rows[0]) == 8
    assert inserted_rows[0][4] == period.label
    assert inserted_rows[0][5:7] == [1200, 0]

@patch("app.repositories.sheet_repo.get_sheet")
def test_insert_rows_after_uses_physical_row_number(mock_get_sheet):
    from app.repositories.sheet_repo import insert_rows_after

    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    rows = [["subtotal"]]

    insert_rows_after("Unit", 4, rows)

    worksheet.insert_rows.assert_called_once_with(rows, row=5)

@patch("app.repositories.sheet_repo.get_sheet")
def test_insert_rows_after_colors_weekly_and_monthly_totals_differently(mock_get_sheet):
    from app.repositories.sheet_repo import insert_rows_after

    worksheet = MagicMock()
    worksheet.id = 42
    mock_get_sheet.return_value = worksheet
    rows = [
        ["2026-10-31", "", "SUBTOTAL MINGGU 4", "", "", 100, 0, 100, ""],
        ["2026-10-31", "", "TOTAL BULAN Oktober 2026", "", "", 100, 0, 100, ""],
    ]

    insert_rows_after("Unit", 10, rows)

    requests = worksheet.spreadsheet.batch_update.call_args.args[0]["requests"]
    colors = [request["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"] for request in requests]
    assert colors == [
        {"red": 1.0, "green": 0.94, "blue": 0.72},
        {"red": 0.82, "green": 0.91, "blue": 0.84},
    ]
    assert [request["repeatCell"]["range"]["startRowIndex"] for request in requests] == [10, 11]

@patch("app.repositories.sheet_repo.get_sheet")
def test_daily_total_style_is_limited_to_label_and_amount_columns(mock_get_sheet):
    from app.repositories.sheet_repo import insert_rows_after

    worksheet = MagicMock()
    worksheet.id = 42
    mock_get_sheet.return_value = worksheet

    insert_rows_after(
        "(2)Rincian Transaksi Harian", 10,
        [["", "", "", "", "SUBTOTAL MINGGU 1", 1200, 0, ""]],
    )

    formatted_range = worksheet.spreadsheet.batch_update.call_args.args[0]["requests"][0]["repeatCell"]["range"]
    assert formatted_range["startColumnIndex"] == 4
    assert formatted_range["endColumnIndex"] == 7

@patch("app.repositories.sheet_repo.insert_rows_after")
@patch("app.services.periodic_total_service.compute_period_total")
@patch("app.repositories.sheet_repo.get_sheet")
@patch("app.services.periodic_total_service.ensure_sorted")
def test_empty_week_totals_stay_in_order_before_later_week_transactions(
    mock_sorted, mock_get_sheet, mock_compute, mock_insert,
):
    from app.services.periodic_total_service import get_period_boundaries

    worksheet = MagicMock()
    mock_get_sheet.return_value = worksheet
    periods = get_period_boundaries(2026, 11)
    worksheet.get_all_values.return_value = [
        ["Industri Pertanian Terpadu Lentera Farm"], [], ["Periode: November 2026"], [],
        ["Tanggal", "Nama Produk /Jasa/ Barang", "Keterangan Transaksi", "Kuantitas", "Satuan (Rp)", "Pemasukan (Rp)", "Pengeluaran (Rp)", "Transaction ID"],
        ["2026-11-16", "Produk", "", "1", "unit", "500", "0", "TX1"],
    ]
    mock_compute.side_effect = lambda sheet, period: PeriodTotal(period, 0, 0, 0)

    write_period_total("(2)Rincian Transaksi Harian", periods[0])
    _, first_after, first_row = mock_insert.call_args.args
    assert first_after == 5
    assert first_row[0][4] == periods[0].label

    worksheet.get_all_values.return_value = [
        *worksheet.get_all_values.return_value[:5],
        first_row[0],
        worksheet.get_all_values.return_value[5],
    ]
    write_period_total("(2)Rincian Transaksi Harian", periods[1])
    _, second_after, second_row = mock_insert.call_args.args
    assert second_after == 6
    assert second_row[0][4] == periods[1].label

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

def test_daily_sale_rows_keep_expected_column_order():
    from app.services.sales_service import _save_sale

    sale = {
        "tanggal": date(2026, 11, 1), "produk": "Kohe Kambing",
        "keterangan": "Pembelian", "kuantitas": 2,
        "unit_kuantitas": "karung", "satuan": "karung",
        "penjualan": 12500, "transaction_id": "20261101-ABC-12345678",
        "unit": "(3)Pertanian ", "is_penjualan": False,
    }
    unit_entry, daily_entry = _save_sale(sale)

    assert unit_entry[1] == [date(2026, 11, 1), "Kohe Kambing", "Pembelian", "2 karung", "karung", "", 12500, -12500, "20261101-ABC-12345678"]
    assert daily_entry[1] == [date(2026, 11, 1), "Kohe Kambing", "Pembelian", "2 karung", "karung", "", 12500, "20261101-ABC-12345678"]
